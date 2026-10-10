"""A thin, generic browser layer for the model: one `Tab` per website, observed as compact text with numbered
elements, driven by element numbers. Nothing here knows any particular website; it only removes the mechanics
(iframes, dialogs, downloads, ZIP files, new tabs, flaky errors) from the model's way.
"""
import asyncio
import io
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path

OBSERVE_JS = (Path(__file__).parent / "observe.js").read_text()
FETCH_JS = """async (url) => {
  const response = await fetch(url, {credentials: "include"});
  const bytes = new Uint8Array(await response.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  const headers = {};
  response.headers.forEach((value, key) => { headers[key] = value; });
  return {status: response.status, headers, data: btoa(binary)};
}"""
FRAME_READ_SECONDS = 10  # reading one frame may take at most this long
MAX_OBSERVATION = 24000  # characters; longer pages are cut and the model can ask for more with read_more


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
            self.skip -= 1

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
        self._attach(page)

    def _attach(self, page):
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
        if new is self.page:
            return
        try:
            await new.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        if new in self.scratch_pages or new.is_closed():
            return
        self.events.append(f"A new tab opened at {new.url}; switched to it")
        self.page = new
        self._attach_page_only(new)

    def _attach_page_only(self, page):
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

    def _on_response(self, response):
        try:
            if response.request.resource_type == "document" and response.frame == self.page.main_frame:
                self.last_status = response.status
        except Exception:  # noqa: BLE001
            pass

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
        await self.settle()
        await self._retry_server_error()
        parts, start = [], 0
        self.aid_frames = {}
        for index, frame in enumerate(self.page.frames):
            prefix = "" if index == 0 else f"f{index}."
            try:
                result = await asyncio.wait_for(  # a frame stuck mid-navigation must not block the whole view
                    frame.evaluate(OBSERVE_JS, {"prefix": prefix, "start": start if index == 0 else 0, "full": True}),
                    timeout=FRAME_READ_SECONDS)
            except Exception as error:  # noqa: BLE001
                reason = "it did not respond in time" if isinstance(error, asyncio.TimeoutError) else error
                parts.append(f"--- frame {index} ({frame.url}) could not be read: {reason}")
                continue
            if index == 0:
                start = result["next"]
                parts.append(result["text"])
            elif result["text"].strip():
                parts.append(f"\n--- frame f{index} (url {frame.url}) ---\n{result['text']}")
            self.aid_frames[prefix] = frame
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

    async def _title(self):
        try:
            return await self.page.title()
        except Exception:  # noqa: BLE001
            return ""

    def _locate(self, aid: str):
        aid = aid.strip().strip("[]")
        if not re.fullmatch(r"(f\d+\.)?\d+", aid):  # the model named the element by its text instead of its number
            return self.page.get_by_text(aid.strip('"'), exact=False).first
        prefix = aid[: aid.index(".") + 1] if "." in aid else ""
        frame = self.aid_frames.get(prefix, self.page.main_frame)
        return frame.locator(f'[data-aid="{aid}"]').first

    async def click(self, aid: str) -> str:
        locator = self._locate(aid)
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
        locator = self._locate(aid)
        await locator.fill(text, timeout=6000)
        if submit:
            await locator.press("Enter")
            await asyncio.sleep(0.6)
        return await self.observe()

    async def select(self, aid: str, option: str) -> str:
        locator = self._locate(aid)
        try:
            await locator.select_option(label=option, timeout=4000)
        except Exception:  # noqa: BLE001
            await locator.select_option(value=option, timeout=4000)
        await asyncio.sleep(0.3)
        return await self.observe()

    async def press(self, key: str) -> str:
        await self.page.keyboard.press(key)
        await asyncio.sleep(0.5)
        return await self.observe()

    async def goto(self, url: str) -> str:
        for attempt in range(3):
            try:
                response = await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
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

    async def fetch_file(self, url: str) -> str:
        """Downloads a URL with this tab's session (cookies) and reads it: for ZIPs and documents behind links."""
        url = url if "://" in url else self._absolute(url)
        status, headers, data = await self._fetch_bytes(url)
        if "html" in headers.get("content-type", ""):
            # a web page: render it in a scratch tab so hidden decoys are filtered like any page view
            scratch = await self.page.context.new_page()
            self.scratch_pages.add(scratch)
            try:
                await scratch.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(0.5)
                parts = []
                for index, frame in enumerate(scratch.frames):
                    result = await frame.evaluate(OBSERVE_JS, {"prefix": f"x{index}.", "start": 0, "full": True})
                    parts.append(result["text"])
                text = "\n".join(parts)
            finally:
                await scratch.close()
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
