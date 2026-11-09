"""Small stand-ins for Playwright, for paths that need no real browser."""
import json

import tracker_core as core


class Response:
    def __init__(self, status=200):
        self.status = status


class FakePage:
    def __init__(self, title="Blinkit", text="Home - 12 MG Road", status=200, url="https://blinkit.com/"):
        self.title_text, self.text, self.status, self.url = title, text, status, url
        self.fail_goto = None

    def goto(self, url, **kwargs):
        if self.fail_goto:
            raise self.fail_goto
        self.url = url
        return Response(self.status)

    def wait_for_timeout(self, ms):
        pass

    def wait_for_selector(self, *a, **k):
        pass

    def title(self):
        return self.title_text

    def screenshot(self, path=None, **kwargs):
        with open(path, "wb") as f:
            f.write(b"png")

    def evaluate(self, script):
        if script == core.PAGE_INFO_JS:
            return {"title": self.title_text, "text": self.text[:6000], "length": len(self.text)}
        if "navigator.userAgent" in script:
            return "Mozilla/5.0 Test Chrome/156"
        return self.text


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.page = page
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler

    def new_page(self):
        return self.page

    def storage_state(self, path=None):
        with open(path, "w") as f:
            json.dump({"cookies": [], "origins": []}, f)


class FakeBrowser:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def is_connected(self):
        return not self.closed


class FakePlaywright:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
