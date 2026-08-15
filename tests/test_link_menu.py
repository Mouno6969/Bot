import unittest

from messenger_bot.messenger import MessengerBot


class FakeLocator:
    def __init__(self, page, name, count=1, visible=True):
        self.page = page
        self.name = name
        self._count = count
        self._visible = visible

    def nth(self, index):
        self.page.selected_indexes.append((self.name, index))
        return FakeLocator(self.page, self.name, 1, self._visible)

    async def count(self):
        return self._count

    async def is_visible(self):
        return self._visible

    async def click(self, **kwargs):
        self.page.clicks.append(self.name)


class FakePage:
    def __init__(self, option_count=10, report_prompt=False, submit_count=1):
        self.option_count = option_count
        self.report_prompt = report_prompt
        self.submit_count = submit_count
        self.clicks = []
        self.selected_indexes = []
        self.option_requests = []
        self.submit_requests = []

    def locator(self, selector):
        if 'Continue' in selector:
            return FakeLocator(self, 'continue')
        return FakeLocator(self, 'more-control')

    async def evaluate(self, script, arg=None):
        if 'uniqueRows' in script:
            self.option_requests.append(arg)
            index = arg['index']
            clicked = index < self.option_count
            return {
                'clicked': clicked,
                'count': self.option_count,
                'labels': [f'plain text row {i + 1}' for i in range(self.option_count)],
            }
        if 'Why are you reporting this profile?' in script:
            return self.report_prompt
        if 'buttons' in script and 'Submit' in script:
            self.submit_requests.append(arg)
            return {
                'ready': self.submit_count == 1,
                'count': self.submit_count,
                'clicked': bool(arg) and self.submit_count == 1,
            }
        return None


class FacebookMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_flow_walks_requested_path_after_continue(self):
        bot = object.__new__(MessengerBot)
        page = FakePage(option_count=10, report_prompt=False)

        await bot._click_facebook_more_menu(page, option_path=(10, 2, 1))

        self.assertEqual(page.clicks, ['more-control', 'continue'])
        self.assertEqual(
            page.option_requests,
            [
                {'index': 1, 'mode': 'menu'},
                {'index': 0, 'mode': 'active'},
                {'index': 9, 'mode': 'active'},
                {'index': 1, 'mode': 'active'},
                {'index': 0, 'mode': 'active'},
            ],
        )

    async def test_live_report_sheet_walks_three_level_path(self):
        bot = object.__new__(MessengerBot)
        page = FakePage(option_count=10, report_prompt=True)

        await bot._click_facebook_more_menu(page, option_path=(2, 4, 1))

        self.assertEqual(page.clicks, ['more-control'])
        self.assertEqual(
            page.option_requests,
            [
                {'index': 1, 'mode': 'menu'},
                {'index': 1, 'mode': 'dialog'},
                {'index': 3, 'mode': 'dialog'},
                {'index': 0, 'mode': 'dialog'},
            ],
        )

    async def test_all_ten_first_level_choices_are_supported(self):
        bot = object.__new__(MessengerBot)
        for option_number in range(1, 11):
            page = FakePage(option_count=10, report_prompt=True)
            await bot._click_facebook_more_menu(page, option_path=(option_number,))
            self.assertEqual(page.option_requests[-1]['index'], option_number - 1)

    async def test_raises_when_scoped_menu_has_no_second_option(self):
        bot = object.__new__(MessengerBot)
        page = FakePage(option_count=1)

        with self.assertRaisesRegex(RuntimeError, 'second scoped text option'):
            await bot._click_facebook_more_menu(page, option_path=(1,))

        self.assertEqual(page.clicks, ['more-control'])

    async def test_submit_mode_waits_for_confirmation_then_submits(self):
        bot = object.__new__(MessengerBot)
        page = FakePage(option_count=10, report_prompt=True)

        self.assertEqual(
            await bot._finish_facebook_submission(page, (2, 4, 1), True, False),
            'awaiting_confirmation',
        )
        self.assertEqual(
            await bot._finish_facebook_submission(page, (2, 4, 1), True, True),
            'submitted',
        )
        self.assertEqual(page.submit_requests, [False, True])

    async def test_normal_mode_never_looks_for_submit(self):
        bot = object.__new__(MessengerBot)
        page = FakePage()

        self.assertEqual(
            await bot._finish_facebook_submission(page, (2,), False, False),
            'selected',
        )
        self.assertEqual(page.submit_requests, [])

    async def test_submit_mode_rejects_ambiguous_submit_controls(self):
        bot = object.__new__(MessengerBot)
        page = FakePage(submit_count=2)

        with self.assertRaisesRegex(RuntimeError, 'not uniquely visible'):
            await bot._finish_facebook_submission(page, (2,), True, False)

    async def test_rejects_empty_or_out_of_range_paths(self):
        bot = object.__new__(MessengerBot)
        page = FakePage()

        with self.assertRaisesRegex(ValueError, 'between 1 and 10'):
            await bot._click_facebook_more_menu(page, option_path=())
        with self.assertRaisesRegex(ValueError, 'between 1 and 10'):
            await bot._click_facebook_more_menu(page, option_path=(2, 11))

        self.assertEqual(page.clicks, [])


if __name__ == '__main__':
    unittest.main()

