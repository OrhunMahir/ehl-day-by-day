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
MAX_OBSERVATION = 24000  # characters; longer pages are cut and the model can ask for more with read_more
LOAD_SECONDS = 15
STUCK_SECONDS = 8
TARGET_SECONDS = 2
ACTION_MILLISECONDS = 6000

TARGET_STATE_JS = """el => {
  if (!el.isConnected) return "stale element: it was removed; use look and choose again";
  if (el.matches(":disabled") || el.closest('[aria-disabled="true"], [inert]')) return "element is disabled";
  if (!(el.checkVisibility ? el.checkVisibility() : el.getClientRects().length)) return "element is not visible";
  const dialogs = [...el.ownerDocument.querySelectorAll('dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"]')]
    .filter(d => d.checkVisibility ? d.checkVisibility() : d.getClientRects().length);
  const contains = (ancestor, child) => {
    while (child) {
      if (child === ancestor) return true;
      child = child.parentNode || (child.getRootNode && child.getRootNode().host);
    }
    return false;
  };
  if (dialogs.some(d => !contains(d, el))) return "element is behind an open dialog; deal with the dialog first";
  return "";
}"""


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
        self.next_aid = 0
        self.frame_prefixes: dict[object, str] = {}
        self.next_frame = 1
        self.last_text = ""
        self.last_status = 0  # HTTP status of the last page load in the main frame
        self.last_method = "GET"
        self.scratch_pages: set = set()
        self.previous: list = []  # pages below the active popup, newest last
        self.allow_recovery = True  # submission disables automatic reloads to preserve a partly completed form
        self.loading: tuple[str, str, float] | None = None
        self.loading_request = None
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
            await self.page.wait_for_load_state("domcontentloaded", timeout=LOAD_SECONDS * 1000)
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

    def _main_document(self, request):
        try:
            return request.resource_type == "document" and request.frame == self.page.main_frame
        except Exception:  # noqa: BLE001
            return False

    def _on_request(self, request):
        if self._main_document(request):
            self.loading = (request.url, request.method, time.monotonic())
            self.loading_request = request

    def _on_request_failed(self, request):
        if request is self.loading_request:
            self.loading = self.loading_request = None

    def _on_response(self, response):
        if self._main_document(response.request):
            self.last_status = response.status
            self.last_method = response.request.method
            if response.request is self.loading_request:
                self.loading = self.loading_request = None

    async def _unstick(self, force=False):
        """Retry an unanswered GET, never replay a form submission or reload while submission owns the tab."""
        if not self.allow_recovery:
            return False
        pending = self.loading
        if pending and (pending[1] != "GET" or (not force and time.monotonic() - pending[2] < STUCK_SECONDS)):
            return False
        if not pending and (not force or self.last_method != "GET"):
            return False
        target = pending[0] if pending else self.page.url
        if not origin(target):
            return False
        self.loading = self.loading_request = None
        try:
            await self.page.goto(target, wait_until="domcontentloaded", timeout=LOAD_SECONDS * 1000)
            self.events.append("An unanswered page load was requested again")
        except Exception as error:  # noqa: BLE001
            self.events.append(f"Navigation retry failed: {str(error).splitlines()[0]}")
        return True

    async def _retry_server_error(self, attempts: int = 2):
        """Flaky server error pages usually work on a retry: reload, at most `attempts` times."""
        if not self.allow_recovery or self.last_method != "GET":
            return
        for attempt in range(attempts):
            if self.last_status < 500:
                return
            status = self.last_status
            await asyncio.sleep(1.5 * (attempt + 1))
            try:
                await self.page.reload(wait_until="domcontentloaded", timeout=LOAD_SECONDS * 1000)
                self.events.append(f"The page answered HTTP {status}; reloaded it")
            except Exception as error:  # noqa: BLE001
                self.events.append(f"The page answered HTTP {status}; reload failed: {str(error).splitlines()[0]}")
                return
        if self.last_status >= 500:
            self.events.append(f"The page still answers HTTP {self.last_status} after retries")

    async def observe(self, offset: int = 0) -> str:
        """The page as text: URL, title, each frame's content with numbered elements, and recent events."""
        await self._ensure_open()
        await self._unstick()
        await self.settle()
        await self._retry_server_error()
        parts, main_read = await self._read_frames()
        if not main_read and await self._unstick(force=True):
            await self.settle()
            parts, main_read = await self._read_frames()
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

    async def _read_frames(self):
        parts, main_read = [], False
        self.aid_frames = {}
        for index, frame in enumerate(self.page.frames):
            if index == 0:
                prefix = ""
            else:
                if frame not in self.frame_prefixes:
                    self.frame_prefixes[frame] = f"f{self.next_frame}."
                    self.next_frame += 1
                prefix = self.frame_prefixes[frame]
            try:
                result = await asyncio.wait_for(  # a frame stuck mid-navigation must not block the whole view
                    frame.evaluate(OBSERVE_JS, {"prefix": prefix, "start": self.next_aid, "full": True}),
                    timeout=FRAME_READ_SECONDS)
            except Exception as error:  # noqa: BLE001
                reason = "it did not respond in time" if isinstance(error, asyncio.TimeoutError) else error
                parts.append(f"--- frame {index} ({frame.url}) could not be read: {reason}")
                continue
            if index == 0:
                main_read = True
                parts.append(result["text"])
            elif result["text"].strip():
                parts.append(f"\n--- frame {prefix[:-1]} (url {frame.url}) ---\n{result['text']}")
            self.next_aid = max(self.next_aid, result["next"])
            self.aid_frames[prefix] = frame
        return parts, main_read

    async def _title(self):
        try:
            return await asyncio.wait_for(self.page.title(), timeout=TARGET_SECONDS)
        except Exception:  # noqa: BLE001
            return ""

    def _locate(self, aid: str):
        """Compatibility locator; public actions use find() to reject missing or ambiguous targets."""
        aid = aid.strip().strip("[]")
        if not re.fullmatch(r"(f\d+\.)?\d+", aid):  # the model named the element by its text instead of its number
            return self.page.get_by_text(aid.strip('"'), exact=False).first
        prefix = aid[: aid.index(".") + 1] if "." in aid else ""
        frame = self.aid_frames.get(prefix)
        if frame is None:
            raise ValueError(f"Stale element {aid}: its frame is no longer in the current view; use look")
        return frame.locator(f'[data-aid="{aid}"]')

    async def find(self, aid: str):
        """Resolve a number or an unambiguous label; never choose the first of several matching controls."""
        await self._ensure_open()
        aid = str(aid).strip().strip("[]")
        if re.fullmatch(r"(f\d+\.)?\d+", aid):
            locator = self._locate(aid)
            count = await locator.count()
            if not count:
                raise ValueError(f"Stale element {aid}: it is no longer present; use look and choose again")
            if count != 1:
                raise ValueError(f"Ambiguous element {aid}: its marker was duplicated; use look and choose again")
            return locator
        text = aid.strip('"')
        tiers = (
            (self.page.get_by_role("button", name=text, exact=True), self.page.get_by_role("link", name=text, exact=True)),
            (self.page.get_by_label(text, exact=True),),
            (self.page.get_by_text(text, exact=True),),
            (self.page.get_by_role("button", name=text), self.page.get_by_role("link", name=text)),
        )
        for tier in tiers:
            matches = []
            for locator in tier:
                visible = locator.filter(visible=True)
                count = await visible.count()
                if count:
                    matches.append((visible, count))
            count = sum(n for _, n in matches)
            if count > 1:
                raise ValueError(f"Ambiguous element {text!r}: {count} controls match; use the number from look")
            if count == 1:
                return matches[0][0]
        raise ValueError(f"No visible element named {text!r}; use look and choose its number")

    async def resolve_target(self, aid: str):
        """Capture this DOM node before a multi-field operation changes the page. Replacement is a stale target."""
        locator = await asyncio.wait_for(self.find(aid), timeout=TARGET_SECONDS)
        handle = await locator.element_handle(timeout=TARGET_SECONDS * 1000)
        if handle is None:
            raise ValueError(f"Stale element {aid}; use look and choose again")
        number = str(aid).strip().strip("[]")
        if re.fullmatch(r"(f\d+\.)?\d+", number):
            assigned = await asyncio.wait_for(handle.evaluate("el => globalThis[Symbol.for('agent-observation-ids')]?.ids.get(el)"),
                                              timeout=TARGET_SECONDS)
            if assigned != number:
                raise ValueError(f"Stale element {aid}: the DOM marker was copied to a different node; use look")
        return handle

    async def check_target(self, handle):
        try:
            frame = await asyncio.wait_for(handle.owner_frame(), timeout=TARGET_SECONDS)
            if frame is None or frame.page is not self.page:
                raise ValueError("Stale element: its page is no longer active; use look")
            reason = await asyncio.wait_for(handle.evaluate(TARGET_STATE_JS), timeout=TARGET_SECONDS)
        except ValueError:
            raise
        except Exception as error:  # noqa: BLE001
            raise ValueError("Stale element: it could not be checked; use look and choose again") from error
        if reason:
            raise ValueError(reason)

    async def click_target(self, handle) -> str:
        """Only a physical, actionable click succeeds. Disabled, stale or covered controls raise an error."""
        await self.check_target(handle)
        await handle.click(timeout=ACTION_MILLISECONDS)
        await asyncio.sleep(0.6)
        return await self.observe()

    async def click(self, aid: str) -> str:
        return await self.click_target(await self.resolve_target(aid))

    async def fill(self, aid: str, text: str, submit: bool = False) -> str:
        handle = await self.resolve_target(aid)
        await self.check_target(handle)
        kind = await asyncio.wait_for(handle.evaluate("el => el.tagName === 'INPUT' ? el.type : ''"), timeout=TARGET_SECONDS)
        if kind in ("checkbox", "radio"):
            value = str(text).strip().lower()
            if value not in ("true", "false", "checked", "unchecked", "1", "0"):
                raise ValueError("For checkbox/radio, type true or false to set its checked state")
            await handle.set_checked(value in ("true", "checked", "1"), timeout=ACTION_MILLISECONDS)
        else:
            await handle.fill(text, timeout=ACTION_MILLISECONDS)
        if submit:
            await handle.press("Enter", timeout=ACTION_MILLISECONDS)
            await asyncio.sleep(0.6)
        return await self.observe()

    async def select(self, aid: str, option: str) -> str:
        handle = await self.resolve_target(aid)
        await self.check_target(handle)
        try:
            await handle.select_option(label=option, timeout=4000)
        except Exception:  # noqa: BLE001
            await handle.select_option(value=option, timeout=4000)
        await asyncio.sleep(0.3)
        return await self.observe()

    async def press(self, key: str) -> str:
        await self._ensure_open()
        await self.page.keyboard.press(key)
        await asyncio.sleep(0.5)
        return await self.observe()

    async def goto(self, url: str) -> str:
        await self._ensure_open()
        for attempt in range(3):
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
        await self.page.go_back(wait_until="domcontentloaded", timeout=LOAD_SECONDS * 1000)
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
        """Read a selected page with shared cookies and same-origin session storage. Gates remain explicit;
        a background read cannot accept a dialog or prove that it reached the requested document."""
        scratch = await self.page.context.new_page()
        self.scratch_pages.add(scratch)
        try:
            await self.copy_session_to(scratch, url)
            if html is not None:  # already fetched: serve it from memory, so the address is not requested twice
                async def serve(route):
                    await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
                await scratch.route(lambda address: address == url, serve, times=1)
            await scratch.goto(url, wait_until="domcontentloaded", timeout=LOAD_SECONDS * 1000)
            await asyncio.sleep(0.5)
            parts = []
            for index, frame in enumerate(scratch.frames):
                try:
                    result = await asyncio.wait_for(
                        frame.evaluate(OBSERVE_JS, {"prefix": f"x{index}.", "start": 0, "full": True}),
                        timeout=FRAME_READ_SECONDS)
                    parts.append(result["text"])
                    gate = await asyncio.wait_for(frame.evaluate("""() => {
                      const visible = el => el.checkVisibility ? el.checkVisibility() : el.getClientRects().length > 0;
                      const modal = [...document.querySelectorAll('dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"]')].some(visible);
                      const password = [...document.querySelectorAll('input[type="password"]')].filter(visible);
                      const login = password.some(el => el.form && [...el.form.querySelectorAll('input')].some(
                        input => visible(input) && !['password','hidden','checkbox','radio','submit','button'].includes(input.type)));
                      return modal ? 'open dialog' : login ? 'sign-in form' : '';
                    }"""), timeout=TARGET_SECONDS)
                    if gate:
                        parts.insert(0, f"ACCESS GATE ({gate}): this background read is incomplete. Use goto on this URL "
                                        "and handle the gate in the active tab; do not treat its document as absent.")
                except Exception as error:  # noqa: BLE001
                    parts.append(f"(a frame could not be read: {type(error).__name__})")
            return re.sub(r"\[x\d+\.\d+\] ", "", "\n".join(parts))
        finally:
            await scratch.close()
            self.scratch_pages.discard(scratch)

    async def copy_session_to(self, page, target_url: str) -> bool:
        """Seed a new page's sessionStorage for this origin only, before navigation; no values are returned/logged.
        Cookies/localStorage are the context owner's responsibility. Gates must still be checked after loading."""
        if not origin(target_url) or origin(target_url) != origin(self.page.url):
            return False
        try:
            storage = await asyncio.wait_for(self.page.evaluate("() => Object.fromEntries(Object.entries(sessionStorage))"),
                                             timeout=TARGET_SECONDS)
            import json
            seed = json.dumps({"origin": origin(target_url), "values": storage}, ensure_ascii=True)
            await page.add_init_script("(() => { const seed = " + seed + "; if (location.origin === seed.origin) "
                                       "for (const [k,v] of Object.entries(seed.values)) sessionStorage.setItem(k,v); })()")
            return True
        except Exception:  # noqa: BLE001
            return False

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
        # Capture the model's selected node before observing; a new snapshot must not reinterpret its number.
        target = await self.resolve_target(aid)
        selected = await asyncio.wait_for(target.evaluate("""el => ({
          kind: el.tagName === 'A' ? 'link' : el.tagName === 'BUTTON' || ['submit','button'].includes(el.type) ? 'button' : el.getAttribute('role') || 'clickable',
          label: (el.innerText || el.value || el.getAttribute('aria-label') || '').replace(/\\s+/g,' ').trim()
        })"""), timeout=TARGET_SECONDS)
        kind, label = selected["kind"], selected["label"]
        if kind not in ("link", "button", "clickable") or not label:
            raise ValueError(f"Element {aid} is not a paging link or button with a label")
        await self.observe()
        views, seen = [self.last_text], set(self.last_text.splitlines())
        urls = [self.page.url]
        for _ in range(max(1, min(int(max_pages), 30)) - 1):
            before = self.last_text
            await self.click_target(target)
            if self.last_text == before:
                break
            fresh = [line for line in self.last_text.splitlines() if line not in seen]
            seen.update(fresh)
            views.append("\n".join(fresh))
            urls.append(self.page.url)
            again = re.findall(r"^\[([\w.]+)\] " + kind + r" \"" + re.escape(label) + r"\"", self.last_text, re.M)
            if not again:
                break
            if len(again) != 1:
                self.events.append("Paging stopped: several controls have the selected label; choose one with look")
                break
            target = await self.resolve_target(again[0])
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
