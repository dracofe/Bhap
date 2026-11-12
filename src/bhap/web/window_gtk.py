"""Standalone native window for the Bhap app (GTK 3 + WebKit2GTK).

Run by a Python that has PyGObject (usually the system /usr/bin/python3), not necessarily the
one Bhap is installed in — so this file must not import anything from Bhap.

    python3 window_gtk.py <url> <title> [icon.svg]

The window only ever shows the local Bhap URL; navigation elsewhere is refused.
"""
import sys
import urllib.parse


def main():
    import gi
    gi.require_version("Gtk", "3.0")
    for version in ("4.1", "4.0"):
        try:
            gi.require_version("WebKit2", version)
            break
        except ValueError:
            continue
    from gi.repository import Gtk, WebKit2

    url, title = sys.argv[1], sys.argv[2]
    icon = sys.argv[3] if len(sys.argv) > 3 else None
    origin = urllib.parse.urlsplit(url)

    settings = Gtk.Settings.get_default()
    if settings is not None:
        settings.set_property("gtk-application-prefer-dark-theme", True)

    window = Gtk.Window(title=title)
    window.set_default_size(1180, 780)
    window.set_wmclass("bhap", "Bhap")
    if icon:
        try:
            window.set_icon_from_file(icon)
        except Exception:  # noqa: BLE001 - icon is cosmetic
            pass

    context = WebKit2.WebContext.get_default()
    try:
        context.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)
    except Exception:  # noqa: BLE001
        pass
    view = WebKit2.WebView.new_with_context(context)
    web_settings = view.get_settings()
    web_settings.set_enable_developer_extras(False)
    web_settings.set_javascript_can_open_windows_automatically(False)
    try:
        web_settings.set_enable_write_console_messages_to_stdout(False)
    except Exception:  # noqa: BLE001
        pass

    def on_policy(_view, decision, decision_type):
        if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            target = urllib.parse.urlsplit(decision.get_navigation_action().get_request().get_uri())
            if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
                decision.ignore()  # stay inside the local app (it is offline anyway)
                return True
        if decision_type == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
            decision.ignore()
            return True
        return False

    view.connect("decide-policy", on_policy)
    view.load_uri(url)
    window.add(view)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
