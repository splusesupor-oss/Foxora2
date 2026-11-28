"""Text-only registered-admin reset: owner auth, stable IDs, no native role RPC."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from handlers import reset_registered_admins as reset
from modules import admin_storage, group_storage
from modules.group_dispatch import classify_priority, LANE_ADMIN
from modules.admin_help_builder import ADMIN_HELP_MESSAGES

CHAT = -1000000000101
OTHER = -1000000000202


class Event:
    is_private = False
    def __init__(self, uid, text, chat=CHAT):
        self.chat_id, self.sender_id = chat, uid
        self.sender = SimpleNamespace(id=uid, username='ioela' if uid == 10 else None)
        self.message = SimpleNamespace(id=101, message=text, edit_date=None)
        self.replies = []
    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace(id=200)


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_admin = (admin_storage.FILE, admin_storage._cache, admin_storage._cache_mtime)
        self.old_group = (group_storage.FILE, group_storage._cache, group_storage._cache_mtime)
        admin_storage.FILE = Path(self.tmp.name) / 'admins.json'
        admin_storage._cache = admin_storage._cache_mtime = None
        group_storage.FILE = Path(self.tmp.name) / 'groups.json'
        group_storage._cache = group_storage._cache_mtime = None
        group_storage.set_group_owner(CHAT, 10)
        group_storage.set_group_owner(OTHER, 11)
        admin_storage.save_admins({
            '101': [
                {'user_id': '10', 'username': 'owner'},
                {'user_id': '99', 'username': 'global'},
                {'user_id': '21', 'username': 'alice', 'name': 'علی'},
                {'user_id': '22', 'username': 'bob', 'name': 'بابک'},
                {'user_id': '23', 'username': 'carol'},
                {'user_id': '70502893', 'username': 'foxora', 'name': 'Foxora.ai'},
                '@legacy_only',
            ],
            '202': [{'user_id': '21', 'username': 'alice'}],
        })
        self.patch_global = patch.object(reset, 'get_owner', return_value={'user_id': 99})
        self.patch_isglobal = patch.object(reset, 'is_global_owner', side_effect=lambda uid: str(uid) == '99')
        self.patch_global.start()
        self.patch_isglobal.start()
        reset._sessions.clear()
        self.bot = SimpleNamespace(bot_account_id=70502893, logger=SimpleNamespace(log_error=lambda _: None))

    def tearDown(self):
        self.patch_isglobal.stop()
        self.patch_global.stop()
        reset._sessions.clear()
        admin_storage.FILE, admin_storage._cache, admin_storage._cache_mtime = self.old_admin
        group_storage.FILE, group_storage._cache, group_storage._cache_mtime = self.old_group
        self.tmp.cleanup()

    async def cmd(self, uid, text, chat=CHAT):
        event = Event(uid, text, chat)
        handled = await reset.handle(self.bot, event, chat, uid, text)
        return handled, event

    def test_selected_admins_only_and_permission_cache_revoked(self):
        async def scenario():
            from handlers import message_handler
            old_cache = dict(message_handler.ADMIN_PERMISSION_CACHE)
            try:
                message_handler.ADMIN_PERMISSION_CACHE.clear()
                message_handler.ADMIN_PERMISSION_CACHE[('101', 21, 'alice')] = (999999, True)
                message_handler.ADMIN_PERMISSION_CACHE[('101', 22, 'bob')] = (999999, True)
                message_handler.ADMIN_PERMISSION_CACHE[('202', 21, 'alice')] = (999999, True)
                denied, ev = await self.cmd(21, 'ریست ادمین ها')
                self.assertTrue(denied)
                self.assertIn('فقط مالک', ev.replies[-1][0])
                opened, ev = await self.cmd(10, 'ریست ادمین ها')
                self.assertTrue(opened)
                panel = ev.replies[-1][0]
                self.assertIn('1_ @alice', panel)
                self.assertIn('2_ @bob', panel)
                self.assertNotIn('شناسه:', panel)
                self.assertIn('4_ @foxora', panel)
                self.assertNotIn('legacy_only', panel)
                self.assertNotIn('انتخاب همه |', panel)
                self.assertNotIn('گزینه ۱ ۲ ۳', panel)
                raw = panel.encode('utf-16-le')
                entities = ev.replies[-1][1]['formatting_entities']
                spans = lambda kind: [raw[e.offset*2:(e.offset+e.length)*2].decode('utf-16-le')
                                      for e in entities if type(e).__name__ == kind]
                self.assertEqual(len(spans('MessageEntityBlockquote')), 2)
                self.assertEqual(spans('MessageEntityBold'),
                                 ['⚠️ : لیست ادمین های ثبت شده', 'تایید همه'])
                self.assertIn('تایید همه', spans('MessageEntityBlockquote')[1])
                self.assertEqual(classify_priority('ریست ادمین ها', ev)[1], LANE_ADMIN)
                selected, ev = await self.cmd(10, 'گزینه ۱ ۳')
                self.assertTrue(selected)
                self.assertIn('2 ادمین انتخاب شد', ev.replies[-1][0])
                self.assertTrue(admin_storage.is_admin(CHAT, 21))  # not before final approval
                self.assertEqual(classify_priority('تایید ریست ادمین ها', ev)[1], LANE_ADMIN)
                confirmed, ev = await self.cmd(10, 'تایید ریست ادمین ها')
                self.assertTrue(confirmed)
                self.assertIn('2 نفر', ev.replies[-1][0])
                self.assertNotIn('علی', ev.replies[-1][0])
                self.assertNotIn('شناسه:', ev.replies[-1][0])
                self.assertFalse(admin_storage.is_admin(CHAT, 21))
                self.assertTrue(admin_storage.is_admin(CHAT, 22))
                self.assertFalse(admin_storage.is_admin(CHAT, 23))
                self.assertTrue(admin_storage.is_admin(OTHER, 21))
                self.assertTrue(admin_storage.is_admin(CHAT, 10))
                self.assertTrue(admin_storage.is_admin(CHAT, 99))
                self.assertTrue(admin_storage.is_admin(CHAT, 70502893))
                self.assertIn('@legacy_only', admin_storage.load_admins()['101'])
                self.assertNotIn(('101', 21, 'alice'), message_handler.ADMIN_PERMISSION_CACHE)
                self.assertIn(('101', 22, 'bob'), message_handler.ADMIN_PERMISSION_CACHE)
                self.assertIn(('202', 21, 'alice'), message_handler.ADMIN_PERMISSION_CACHE)
            finally:
                message_handler.ADMIN_PERMISSION_CACHE.clear()
                message_handler.ADMIN_PERMISSION_CACHE.update(old_cache)
        asyncio.run(scenario())

    def test_global_owner_all_cancel_reconfirm_and_stale_selection(self):
        async def scenario():
            opened, ev = await self.cmd(99, 'ریست ادمین ها')
            self.assertTrue(opened)
            self.assertTrue(reset.is_awaiting(CHAT, 99))
            self.assertTrue((await self.cmd(99, 'انتخاب همه'))[0])
            self.assertTrue((await self.cmd(99, 'لغو انتخاب ۲'))[0])
            self.assertTrue((await self.cmd(99, 'لغو انتخاب ها'))[0])
            self.assertTrue((await self.cmd(99, 'بازگشت'))[0])
            self.assertTrue(admin_storage.is_admin(CHAT, 21))
            self.assertFalse(reset.is_awaiting(CHAT, 99))
            await self.cmd(99, 'ریست ادمین ها')
            # Concurrent admin removal never changes index->ID mapping to a new user.
            admin_storage.remove_admin(CHAT, 21)
            confirmed, ev = await self.cmd(99, 'تایید همه')
            self.assertTrue(confirmed)
            self.assertIn('3 نفر', ev.replies[-1][0])
            self.assertFalse(admin_storage.is_admin(CHAT, 22))
            self.assertFalse(admin_storage.is_admin(CHAT, 23))
            self.assertTrue(admin_storage.is_admin(CHAT, 10))
            self.assertTrue(admin_storage.is_admin(CHAT, 99))
            self.assertFalse(admin_storage.is_admin(CHAT, 70502893))
            self.assertTrue(admin_storage.is_admin(OTHER, 21))
        asyncio.run(scenario())

    def test_invalid_indices_and_group_scope_never_reset(self):
        async def scenario():
            denied, ev = await self.cmd(10, 'ریست ادمین ها', OTHER)
            self.assertTrue(denied)
            self.assertIn('فقط مالک', ev.replies[-1][0])
            await self.cmd(10, 'ریست ادمین ها')
            invalid, ev = await self.cmd(10, 'گزینه ۵')
            self.assertTrue(invalid)
            self.assertIn('شمارهٔ گزینه معتبر نیست', ev.replies[-1][0])
            self.assertTrue(admin_storage.is_admin(CHAT, 21))
            # Another user cannot confirm the group owner's selection.
            self.assertFalse((await self.cmd(22, 'تایید همه'))[0])
            self.assertTrue(admin_storage.is_admin(CHAT, 21))
            self.assertTrue((await self.cmd(10, 'لغو'))[0])
            self.assertFalse((await self.cmd(10, 'تایید همه'))[0])
            self.assertTrue(admin_storage.is_admin(CHAT, 21))
        asyncio.run(scenario())

    def test_batch_saves_once_and_preserves_native_roles(self):
        async def scenario():
            await self.cmd(10, 'ریست ادمین ها')
            with patch.object(admin_storage, 'save_admins', wraps=admin_storage.save_admins) as save:
                ok, result = await self.cmd(10, 'تایید همه')
            self.assertTrue(ok)
            self.assertEqual(save.call_count, 1)
            self.assertIn('انجام دهنده : @ioela', result.replies[-1][0])
            self.assertEqual([row['user_id'] for row in admin_storage.load_admins()['101']
                              if isinstance(row, dict)], ['10', '99'])
        asyncio.run(scenario())

    def test_failed_save_and_unauthorized_no_mutation(self):
        async def scenario():
            await self.cmd(22, 'ریست ادمین ها')
            self.assertEqual(len(admin_storage.load_admins()['101']), 7)
            await self.cmd(10, 'ریست ادمین ها')
            await self.cmd(10, 'گزینه ۱')
            with patch.object(admin_storage, 'save_admins', side_effect=OSError('disk full')):
                ok, ev = await self.cmd(10, 'تایید ریست ادمین ها')
            self.assertTrue(ok)
            self.assertIn('ذخیره‌سازی انجام نشد', ev.replies[-1][0])
            self.assertTrue(admin_storage.is_admin(CHAT, 21))
        asyncio.run(scenario())

    def test_no_eligible_admins_reports_without_session(self):
        async def scenario():
            admin_storage.save_admins({'101': [
                {'user_id': '10'}, {'user_id': '99'},
                '@legacy_only']})
            handled, event = await self.cmd(10, 'ریست ادمین ها')
            self.assertTrue(handled)
            self.assertIn('وجود ندارد', event.replies[-1][0])
            self.assertFalse(reset.is_awaiting(CHAT, 10))
        asyncio.run(scenario())

    def test_stale_self_registration_can_be_reset_and_registered_again(self):
        async def scenario():
            # A leftover internal entry for the bot itself must not be hidden:
            # it blocks a later «ثبت ادمین» with "already registered".
            admin_storage.save_admins({'101': [
                {'user_id': '10', 'username': 'owner'},
                {'user_id': '99', 'username': 'global'},
                {'user_id': '70502893', 'username': 'foxora'},
            ]})
            opened, ev = await self.cmd(10, 'ریست ادمین ها')
            self.assertTrue(opened)
            self.assertIn('1_ @foxora', ev.replies[-1][0])
            confirmed, result = await self.cmd(10, 'تایید همه')
            self.assertTrue(confirmed)
            self.assertIn('1 نفر', result.replies[-1][0])
            self.assertFalse(admin_storage.is_admin(CHAT, 70502893))
            self.assertTrue(admin_storage.add_admin(CHAT, 70502893, username='foxora'))
            self.assertTrue(admin_storage.is_admin(CHAT, 10))
            self.assertTrue(admin_storage.is_admin(CHAT, 99))
        asyncio.run(scenario())

    def test_missing_stored_username_uses_same_admin_list_resolver(self):
        async def scenario():
            admin_storage.save_admins({'101': [
                {'user_id': '10'}, {'user_id': '99'},
                {'user_id': '70502893', 'name': 'Foxora.ai', 'username': None},
            ]})
            client = SimpleNamespace(get_entity=AsyncMock(return_value=SimpleNamespace(
                id=70502893, username='foxora', first_name='Foxora')))
            self.bot.client = client
            ok, ev = await self.cmd(10, 'ریست ادمین ها')
            self.assertTrue(ok)
            self.assertIn('1_ @foxora', ev.replies[-1][0])
            client.get_entity.assert_awaited_once_with(70502893)
        asyncio.run(scenario())

    def test_router_is_before_other_confirmation_workflows(self):
        import inspect
        from handlers import message_handler
        source = inspect.getsource(message_handler.handle_new_message)
        hook = source.index('reset_registered_admins.handle(')
        self.assertLess(hook, source.index('_two_factor_gate('))
        self.assertLess(hook, source.index('_full_purge.claims('))
        self.assertLess(hook, source.index('_ad_spam_guard.handle_confirm('))

    def test_help_is_in_same_two_messages_with_real_entities(self):
        self.assertEqual(len(ADMIN_HELP_MESSAGES), 2)
        matches = [(text, entities) for text, entities in ADMIN_HELP_MESSAGES
                   if '🧻 برای ریست کامل ادمین های ثبت شده' in text]
        self.assertEqual(len(matches), 1)
        text, entities = matches[0]
        def spans(kind):
            raw = text.encode('utf-16-le')
            return [raw[e.offset*2:(e.offset+e.length)*2].decode('utf-16-le')
                    for e in entities if type(e).__name__ == kind]
        self.assertIn('🧻 برای ریست کامل ادمین های ثبت شده', spans('MessageEntityBold'))
        self.assertIn('ریست ادمین ها', spans('MessageEntityBold'))
        self.assertTrue(any('ریست ادمین ها' in span
                            for span in spans('MessageEntityBlockquote')))


if __name__ == '__main__':
    unittest.main()
