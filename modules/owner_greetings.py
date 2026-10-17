"""Exact, whitespace-tolerant greetings reserved for a registered group owner."""


def normalize_owner_greeting(text):
    """Normalize only spaces and Persian half-spaces; do not alter message meaning."""
    return " ".join(str(text or "").replace("\u200c", " ").split())


_OWNER_GREETING_RESPONSES = {
    "سلام": "سلام مالک جونم 🫶",
    "خوبی ربات": "شما خوب باشی خوبم 🫴",
    "چطوری": "شما خوب باشی خوبم 🫴",
    "چخبر ربات": "سلامتی مالک 🫶",
    "چخبرا": "سلامتی مالک 🫶",
    "چخبر": "سلامتی مالک 🫶",
    "چ خبر": "سلامتی مالک 🫶",
    "چه خبر": "سلامتی مالک 🫶",
}


def owner_greeting_response(text):
    return _OWNER_GREETING_RESPONSES.get(normalize_owner_greeting(text))


def registered_owner_greeting_response(text, user_id, registered_owner_id, *, is_private=False):
    """Returns a special reply only for the owner explicitly registered in this group."""
    if is_private or registered_owner_id is None:
        return None
    if str(user_id) != str(registered_owner_id):
        return None
    return owner_greeting_response(text)
