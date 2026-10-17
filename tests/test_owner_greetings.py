import unittest

from modules.owner_greetings import registered_owner_greeting_response


class OwnerGreetingTests(unittest.TestCase):
    OWNER_ID = 101
    ADMIN_ID = 202
    USER_ID = 303

    def test_registered_owner_receives_exact_replies(self):
        expected = {
            "سلام": "سلام مالک جونم 🫶",
            "خوبی ربات": "شما خوب باشی خوبم 🫴",
            "چطوری": "شما خوب باشی خوبم 🫴",
            "چخبر ربات": "سلامتی مالک 🫶",
            "چخبرا": "سلامتی مالک 🫶",
            "چخبر": "سلامتی مالک 🫶",
            "چ خبر": "سلامتی مالک 🫶",
            "چه خبر": "سلامتی مالک 🫶",
        }
        for message, response in expected.items():
            self.assertEqual(
                registered_owner_greeting_response(message, self.OWNER_ID, self.OWNER_ID),
                response,
            )

    def test_extra_spaces_and_half_spaces_are_normalized(self):
        self.assertEqual(
            registered_owner_greeting_response("  چه\u200c خبر  ", self.OWNER_ID, self.OWNER_ID),
            "سلامتی مالک 🫶",
        )
        self.assertEqual(
            registered_owner_greeting_response("خوبی   ربات", self.OWNER_ID, self.OWNER_ID),
            "شما خوب باشی خوبم 🫴",
        )

    def test_admin_and_regular_user_receive_no_special_reply(self):
        for user_id in (self.ADMIN_ID, self.USER_ID):
            self.assertIsNone(
                registered_owner_greeting_response("سلام", user_id, self.OWNER_ID)
            )

    def test_global_owner_without_group_registration_receives_no_special_reply(self):
        self.assertIsNone(
            registered_owner_greeting_response("سلام", self.OWNER_ID, None)
        )

    def test_private_chat_receives_no_special_reply(self):
        self.assertIsNone(
            registered_owner_greeting_response(
                "سلام", self.OWNER_ID, self.OWNER_ID, is_private=True
            )
        )


if __name__ == "__main__":
    unittest.main()
