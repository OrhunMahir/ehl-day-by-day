"""A thin, generic browser layer for the model: one `Tab` per website, observed as compact text with numbered
elements, driven by element numbers. Nothing here knows any particular website; it only removes the mechanics
(iframes, dialogs, downloads, ZIP files, new tabs, flaky errors) from the model's way.
"""
import asyncio
import io
import re
import time
import zipfile
from html.parser import HTMLParser
from pathlib import Path

OBSERVE_JS = (Path(__file__).parent / "observe.js").read_text(encoding="utf-8")
FETCH_JS = """async (url) => {
  const response = await fetch(url, {credentials: "include"});
  const bytes = new Uint8Array(await response.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  const headers = {};
  response.headers.forEach((value, key) => { headers[key] = value; });
  return {status: response.status, headers, data: btoa(binary)};
}"""
READ_PAGES_MAX = 6  # pages per read_pages call
READ_PAGES_EACH = 8000  # characters kept per page in read_pages
FRAME_READ_SECONDS = 10  # reading one frame may take at most this long
PENDING_READ_SECONDS = 2  # and this long while a page load is still waiting for the website's answer
TITLE_SECONDS = 2  # reading the page title may take at most this long
MAX_OBSERVATION = 24000  # characters; longer pages are cut and the model can ask for more with read_more
# One attempt of a page load may take this long; `goto` makes two, which stays under the browsing agent's limit for
# one action. Reading a page never requests a load again: while one hangs, the view says so and stays quick, and a
# website that keeps not answering gets a fresh session (Tab.reopen) from the agent.
LOAD_SECONDS = 20


class _HtmlText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("tr", "p", "div", "br", "li", "h1", "h2", "h3", "h4", "table", "section"):
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)  # a stray closing tag must not hide the rest

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _HtmlText()
    parser.feed(html)
    text = "".join(parser.parts)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def file_to_text(name: str, data: bytes) -> str:
    """Text of a downloaded file; ZIP archives are opened and each member rendered."""
    lower = name.lower()
    if lower.endswith(".zip") or data[:2] == b"PK":
        out = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.namelist():
                if member.endswith("/"):
                    continue
                shown = member
                try:  # names stored without the UTF-8 flag come back as cp437
                    shown = member.encode("cp437").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
                out.append(f"===== {shown} =====\n{file_to_text(shown, archive.read(member))}")
        return "\n".join(out)
    if lower.endswith(".pdf") or data[:4] == b"%PDF":
        try:
            from pypdf import PdfReader
            return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
        except Exception as error:  # noqa: BLE001
            return f"(PDF could not be read: {error})"
    text = data.decode("utf-8", errors="replace")
    if lower.endswith((".html", ".htm", ".xhtml")) or "<html" in text[:500].lower():
        return html_to_text(text)
    return text


def origin(url: str) -> str:
    """scheme://host[:port] of a URL, lower-cased; "" for about:blank and the like."""
    from urllib.parse import urlsplit
    parts = urlsplit(url or "")
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme in ("http", "https") and parts.netloc else ""


class Tab:
    """One website in one browser page."""

    homes: dict[str, str] = {}  # site id -> the origin its tab started on, for every tab of this run

    def __init__(self, site_id: str, page, log=print):
        self.site_id = site_id
        self.page = page
        self.home = origin(page.url)
        if self.home:
            Tab.homes[site_id] = self.home
        self.log = log
        self.files: dict[str, str] = {}  # downloaded file name -> text
        self.events: list[str] = []  # dialogs, downloads, new tabs since the last observation
        self.aid_frames: dict[str, object] = {}
        self.last_text = ""
        self.last_status = 0  # HTTP status of the last page load in the main frame
        self.scratch_pages: set = set()
        self.previous: list = []  # pages below the active popup, newest last
        self.loading: tuple[str, str, float] | None = None  # the page request in flight: address, method, since
        self._attach(page)

    def _attach(self, page):
        page.on("request", self._on_request)
        page.on("requestfailed", self._on_request_failed)
        page.on("response", self._on_response)
        page.on("dialog", lambda dialog: asyncio.ensure_future(self._on_dialog(dialog)))
        page.on("download", lambda download: asyncio.ensure_future(self._on_download(download)))
        page.context.on("page", lambda new: asyncio.ensure_future(self._on_new_page(new)))

    async def _on_dialog(self, dialog):
        # the model only reaches a dialog by clicking something on purpose, so it is accepted; prompts get ""
        self.events.append(f"Browser dialog ({dialog.type}) said: {dialog.message!r} -> accepted")
        try:
            await dialog.accept()
        except Exception:  # noqa: BLE001
            pass

    async def _on_download(self, download):
        name = download.suggested_filename
        try:
            path = await download.path()
            data = Path(path).read_bytes() if path else b""
            self.files[name] = file_to_text(name, data)
            self.events.append(f"Downloaded file {name!r} ({len(data)} bytes); read it with read_file")
        except Exception as error:  # noqa: BLE001
            self.events.append(f"Download of {name!r} failed: {error}")

    async def _on_new_page(self, new):
        if new is self.page or new in self.scratch_pages:
            return
        try:  # follow only popups this tab opened, not other tabs sharing the browser
            opener = await new.opener()
        except Exception:  # noqa: BLE001
            opener = None
        if opener is not self.page and opener not in self.previous:
            return
        try:
            await new.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        if new in self.scratch_pages or new.is_closed():
            return
        self.events.append(f"A new tab opened at {new.url}; switched to it")
        self.previous.append(self.page)
        self.page = new
        self._attach_page_only(new)
        new.on("close", self._on_close)

    def _on_close(self, page):
        """A popup that closes itself (e.g. a search help after a selection): go back to the page below it."""
        if page is not self.page:
            return
        while self.previous and self.previous[-1].is_closed():
            self.previous.pop()
        if self.previous:
            self.page = self.previous.pop()
            self.events.append("The popup closed; back on the page that opened it")

    async def _ensure_open(self):
        """The active page must be open: fall back to an earlier page of this tab, or reopen the site."""
        if not self.page.is_closed():
            return
        while self.previous and self.previous[-1].is_closed():
            self.previous.pop()
        if self.previous:
            self.page = self.previous.pop()
            self.events.append("The active page had closed; back on the previous page")
            return
        self.page = await self.page.context.new_page()
        self._attach_page_only(self.page)
        if self.home:
            await self.page.goto(self.home, wait_until="domcontentloaded")
        self.events.append("The active page had closed; opened the website again")

    def _attach_page_only(self, page):
        page.on("request", self._on_request)
        page.on("requestfailed", self._on_request_failed)
        page.on("response", self._on_response)
        page.on("dialog", lambda dialog: asyncio.ensure_future(self._on_dialog(dialog)))
        page.on("download", lambda download: asyncio.ensure_future(self._on_download(download)))

    async def settle(self):
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.4)
        # embedded documents (viewers, mail bodies) often load late, e.g. once a collapsed section is opened
        frames = self.page.frames[1:]
        if frames:
            try:
                await asyncio.wait_for(asyncio.gather(*(f.wait_for_load_state("domcontentloaded") for f in frames),
                                                      return_exceptions=True), timeout=3)
            except asyncio.TimeoutError:
                pass

    def _main_document(self, request) -> bool:
        try:
            return request.resource_type == "document" and request.frame == self.page.main_frame
        except Exception:  # noqa: BLE001 - e.g. a service worker's request has no frame
            return False

    def _on_request(self, request):
        if self._main_document(request):
            self.loading = (request.url, request.method, time.time())

    def _on_request_failed(self, request):
        if self._main_document(request) and self.loading and self.loading[0] == request.url:
            self.loading = None

    def _on_response(self, response):
        if self._main_document(response.request):
            self.last_status = response.status
            self.loading = None

    async def reopen(self, browser=None) -> str:
        """A fresh session for this website: a new browser context (no cookies, new connections) in `browser`, or in
        the tab's own browser, opened at the website's home address. The Tab object stays the same for whoever holds
        it. Raises when the website does not answer there either."""
        browser = browser or self.page.context.browser
        context = await browser.new_context()
        page = await context.new_page()
        self.page, self.previous, self.loading, self.last_status = page, [], None, 0
        self._attach(page)
        await page.goto(self.home or "about:blank", wait_until="domcontentloaded", timeout=LOAD_SECONDS * 1000)
        self.events.append("The website had not answered; it is open again in a fresh session (sign in again)")
        return await self.observe()

    async def _retry_server_error(self, attempts: int = 2):
        """Flaky server error pages usually work on a retry: reload, at most `attempts` times."""
        for attempt in range(attempts):
            if self.last_status < 500:
                return
            status = self.last_status
            await asyncio.sleep(1.5 * (attempt + 1))
            try:
                await self.page.reload(wait_until="domcontentloaded", timeout=30000)
                self.events.append(f"The page answered HTTP {status}; reloaded it")
            except Exception as error:  # noqa: BLE001
                self.events.append(f"The page answered HTTP {status}; reload failed: {str(error).splitlines()[0]}")
                return
        if self.last_status >= 500:
            self.events.append(f"The page still answers HTTP {self.last_status} after retries")

    async def observe(self, offset: int = 0) -> str:
        """The page as text: URL, title, each frame's content with numbered elements, and recent events."""
        await self._ensure_open()
        await self.settle()
        await self._retry_server_error()
        # a frame in the middle of a load answers only once the load is done: with one pending, read briefly
        parts, _ = await self._read_frames(PENDING_READ_SECONDS if self.loading else FRAME_READ_SECONDS)
        if self.loading:  # a page load still waits for the website's answer: say so, never wait for it here
            url, _, since = self.loading
            parts.insert(0, f"(still loading {url} for {time.time() - since:.0f} s: the website has not answered yet; "
                            "look again later, or goto the address to ask again)")
        text = "\n".join(parts)
        self.last_text = text
        header = f"[{self.site_id}] URL: {self.page.url}\nTitle: {await self._title()}\n"
        if self.events:
            header += "Events: " + " | ".join(self.events) + "\n"
            self.events = []
        if self.files:
            header += "Files available to read_file: " + ", ".join(self.files) + "\n"
        body = text[offset:offset + MAX_OBSERVATION]
        if offset + MAX_OBSERVATION < len(text):
            body += f"\n... (page continues: {len(text) - offset - MAX_OBSERVATION} more characters; call read_more with offset={offset + MAX_OBSERVATION})"
        return header + body

    async def _read_frames(self, timeout: float = FRAME_READ_SECONDS) -> tuple[list[str], bool]:
        """Every frame's text, and whether the main frame could be read."""
        parts, start, main_read = [], 0, False
        self.aid_frames = {}
        for index, frame in enumerate(self.page.frames):
            prefix = "" if index == 0 else f"f{index}."
            try:
                result = await asyncio.wait_for(  # a frame stuck mid-navigation must not block the whole view
                    frame.evaluate(OBSERVE_JS, {"prefix": prefix, "start": start if index == 0 else 0, "full": True}),
                    timeout=timeout)
            except Exception as error:  # noqa: BLE001
                reason = "it did not respond in time" if isinstance(error, asyncio.TimeoutError) else error
                parts.append(f"--- frame {index} ({frame.url}) could not be read: {reason}")
                if index == 0 and isinstance(error, asyncio.TimeoutError):
                    break  # the page itself is stuck: its embedded frames would only add their own waits
                continue
            if index == 0:
                start, main_read = result["next"], True
                parts.append(result["text"])
            elif result["text"].strip():
                parts.append(f"\n--- frame f{index} (url {frame.url}) ---\n{result['text']}")
            self.aid_frames[prefix] = frame
        return parts, main_read

    async def _title(self):
        # a page whose script keeps the browser busy never answers: without a limit the whole view waited until the
        # action's time ran out (60 s, four times in a row in a test run)
        try:
            return await asyncio.wait_for(self.page.title(), timeout=TITLE_SECONDS)
        except Exception:  # noqa: BLE001
            return ""

    def _locate(self, aid: str):
        aid = aid.strip().strip("[]")
        if not re.fullmatch(r"(f\d+\.)?\d+", aid):  # the model named the element by its text instead of its number
            return self.page.get_by_text(aid.strip('"'), exact=False).first
        prefix = aid[: aid.index(".") + 1] if "." in aid else ""
        frame = self.aid_frames.get(prefix, self.page.main_frame)
        return frame.locator(f'[data-aid="{aid}"]').first

    async def find(self, aid: str):
        """The element the model named: by its number, or by its visible text. A text name means a button or link
        with exactly that name first, then an element with exactly that text, and only then one containing it, so
        that "Next" is the button and not a hint that mentions it."""
        aid = aid.strip().strip("[]")
        if re.fullmatch(r"(f\d+\.)?\d+", aid):
            return self._locate(aid)
        text = aid.strip('"')
        page = self.page
        for locator in (page.get_by_role("button", name=text, exact=True), page.get_by_role("link", name=text, exact=True),
                        page.get_by_text(text, exact=True), page.get_by_role("button", name=text),
                        page.get_by_role("link", name=text)):
            try:
                visible = locator.filter(visible=True)
                if await visible.count():
                    return visible.first
            except Exception:  # noqa: BLE001 - try the next way of finding it
                continue
        return page.get_by_text(text, exact=False).first

    async def click(self, aid: str) -> str:
        await self._ensure_open()
        locator = await self.find(aid)
        try:
            await locator.scroll_into_view_if_needed(timeout=3000)
        except Exception:  # noqa: BLE001
            pass
        try:
            await locator.click(timeout=6000)
        except Exception:  # noqa: BLE001
            await locator.evaluate("el => el.click()")  # covered or off-screen: a DOM click still works
        await asyncio.sleep(0.6)
        return await self.observe()

    async def fill(self, aid: str, text: str, submit: bool = False) -> str:
        await self._ensure_open()
        locator = self._locate(aid)
        await locator.fill(text, timeout=6000)
        if submit:
            await locator.press("Enter")
            await asyncio.sleep(0.6)
        return await self.observe()

    async def select(self, aid: str, option: str) -> str:
        await self._ensure_open()
        locator = self._locate(aid)
        try:
            await locator.select_option(label=option, timeout=4000)
        except Exception:  # noqa: BLE001
            await locator.select_option(value=option, timeout=4000)
        await asyncio.sleep(0.3)
        return await self.observe()

    async def press(self, key: str) -> str:
        await self._ensure_open()
        await self.page.keyboard.press(key)
        await asyncio.sleep(0.5)
        return await self.observe()

    async def goto(self, url: str) -> str:
        await self._ensure_open()
        for attempt in range(2):
            try:
                response = await self.page.goto(url, wait_until="domcontentloaded", timeout=LOAD_SECONDS * 1000)
                if response is None or response.status < 500:
                    break
            except Exception as error:  # noqa: BLE001
                if "Download is starting" in str(error):
                    await asyncio.sleep(1)
                    break
                self.events.append(f"Navigation error: {error}".splitlines()[0])
            await asyncio.sleep(1.5 * (attempt + 1))
        return await self.observe()

    async def back(self) -> str:
        await self._ensure_open()
        await self.page.go_back()
        return await self.observe()

    async def _fetch_bytes(self, url: str) -> tuple[int, dict, bytes]:
        """Fetches from inside the page, so the request has the page's cookies, proxy and network exactly as the
        browser sees them. A cross-origin URL the page may not read falls back to Playwright's own client."""
        import base64
        try:
            result = await self.page.evaluate(FETCH_JS, url)
            return result["status"], {k.lower(): v for k, v in result["headers"].items()}, base64.b64decode(result["data"])
        except Exception:  # noqa: BLE001
            response = await self.page.context.request.get(url)
            return response.status, response.headers, await response.body()

    async def _render_readonly(self, url: str, html: bytes | None = None) -> str:
        """A page rendered in a background tab with this tab's session, as text: hidden decoys are filtered like in
        any page view. Element numbers are left out (the tab closes); links keep their target."""
        scratch = await self.page.context.new_page()
        self.scratch_pages.add(scratch)
        try:
            if html is not None:  # already fetched: serve it from memory, so the address is not requested twice
                async def serve(route):
                    await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
                await scratch.route(lambda address: address == url, serve, times=1)
            await scratch.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(0.5)
            parts = []
            for index, frame in enumerate(scratch.frames):
                try:
                    result = await asyncio.wait_for(
                        frame.evaluate(OBSERVE_JS, {"prefix": f"x{index}.", "start": 0, "full": True}),
                        timeout=FRAME_READ_SECONDS)
                    parts.append(result["text"])
                except Exception as error:  # noqa: BLE001
                    parts.append(f"(a frame could not be read: {type(error).__name__})")
            return re.sub(r"\[x\d+\.\d+\] ", "", "\n".join(parts))
        finally:
            await scratch.close()

    async def read_pages(self, urls: list[str]) -> str:
        """Several pages the model chose, read in parallel in background tabs and returned together."""
        urls = [u if "://" in u else self._absolute(u) for u in urls][:READ_PAGES_MAX]

        async def one(url):
            try:
                text = await asyncio.wait_for(self._render_readonly(url), timeout=45)
            except Exception as error:  # noqa: BLE001
                text = f"(could not be read: {type(error).__name__} {str(error).splitlines()[0][:200] if str(error) else ''})"
            if len(text) > READ_PAGES_EACH:
                text = text[:READ_PAGES_EACH] + f"\n... (cut; open it with goto to read all {len(text)} characters)"
            return f"===== {url} =====\n{text}"
        joined = "\n".join(await asyncio.gather(*(one(u) for u in urls)))
        self.last_text = joined
        body = joined[:MAX_OBSERVATION]
        if len(joined) > MAX_OBSERVATION:
            body += f"\n... (continues: read_more with offset={MAX_OBSERVATION})"
        return f"Read {len(urls)} pages (read-only; to act on one, goto it):\n" + body

    async def fetch_file(self, url: str) -> str:
        """Downloads a URL with this tab's session (cookies) and reads it: for ZIPs and documents behind links."""
        url = url if "://" in url else self._absolute(url)
        status, headers, data = await self._fetch_bytes(url)
        if "html" in headers.get("content-type", ""):
            text = await self._render_readonly(url, html=data)
            return f"HTTP {status}, page {url} (read-only view; open it with goto to interact):\n" + text[:MAX_OBSERVATION]
        name = url.rstrip("/").split("/")[-1].split("?")[0] or "download"
        disposition = headers.get("content-disposition", "")
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition)
        if match:
            name = match.group(1)
        self.files[name] = file_to_text(name, data)
        return f"HTTP {status}, file {name!r}:\n" + self.files[name][:MAX_OBSERVATION]

    def _absolute(self, path: str) -> str:
        from urllib.parse import urljoin
        return urljoin(self.page.url, path)

    def read_file(self, name: str, offset: int = 0) -> str:
        if name not in self.files:
            return f"No file {name!r}. Available: {', '.join(self.files) or 'none'}"
        text = self.files[name]
        body = text[offset:offset + MAX_OBSERVATION]
        if offset + MAX_OBSERVATION < len(text):
            body += f"\n... (continues; read_file with offset={offset + MAX_OBSERVATION})"
        return body

    async def collect_pages(self, aid: str, max_pages: int = 10) -> str:
        """Follows a paging control the model chose ("next page", "load more") until it disappears or `max_pages`
        views are collected, and joins them. The control is found again on each new view by its visible text."""
        first = await self.observe()
        match = re.search(r"^\[" + re.escape(aid.strip().strip("[]")) + r"\] (link|button|clickable) \"([^\"]*)\"", self.last_text, re.M)
        if not match or not match.group(2).strip():
            return f"Element {aid} is not a link or button with text on this page.\n" + first
        kind, label = match.group(1), match.group(2)
        views, seen = [self.last_text], set(self.last_text.splitlines())
        urls = [self.page.url]
        current = aid
        for _ in range(max(1, min(int(max_pages), 30)) - 1):
            before = self.last_text
            await self.click(current)
            if self.last_text == before:
                break
            fresh = [line for line in self.last_text.splitlines() if line not in seen]
            seen.update(fresh)
            views.append("\n".join(fresh))
            urls.append(self.page.url)
            again = re.search(r"^\[([\w.]+)\] " + kind + r" \"" + re.escape(label) + r"\"", self.last_text, re.M)
            if not again:
                break
            current = again.group(1)
        joined = "\n".join(f"--- view {i + 1} ({u}) ---\n{v}" for i, (u, v) in enumerate(zip(urls, views)))
        self.last_text = joined
        note = (f"Collected {len(views)} views by following {kind} \"{label}\". Later views show only lines that are "
                "new. Element numbers are valid only for the last view (use look to act on it).\n")
        body = joined[:MAX_OBSERVATION]
        if len(joined) > MAX_OBSERVATION:
            body += f"\n... (continues: read_more with offset={MAX_OBSERVATION})"
        return note + body

    def read_more(self, offset: int) -> str:
        text = self.last_text[offset:offset + MAX_OBSERVATION]
        if offset + MAX_OBSERVATION < len(self.last_text):
            text += f"\n... (continues; read_more offset={offset + MAX_OBSERVATION})"
        return text
