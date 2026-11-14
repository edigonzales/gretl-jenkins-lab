#!/usr/bin/env python3
"""Regression tests for cleanup during a Jenkins queue handoff."""
import importlib.util
import unittest
from itertools import count
from pathlib import Path
from unittest.mock import Mock, call, patch

spec = importlib.util.spec_from_file_location("j4_queue", Path(__file__).with_name("j4-queue.py"))
queue_test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue_test)

JOB = "/job/gretl-dev-csv-publication/"
QUEUE = "/queue/item/42/"
BUILD = JOB + "12/"
BUILDS_API = JOB + "api/json?tree=builds[queueId,url]"
BUILD_API = BUILD + "api/json?tree=building"
CANCEL = "/queue/cancelItem?id=42"


class QueueHandoffTest(unittest.TestCase):
    def test_recovers_when_promotion_outpaces_cancel_but_build_is_not_yet_visible(self):
        for status in (404, 500):
            with self.subTest(status=status), patch.object(queue_test.e2e.time, "sleep"):
                admin = Mock()
                admin.call.side_effect = [queue_test.e2e.JenkinsError(CANCEL, status, (204,)), None]
                admin.json.side_effect = [
                    {"blocked": True},
                    {"builds": [{"queueId": 41, "url": JOB + "11/"}]},
                    queue_test.e2e.JenkinsError(QUEUE, 404, (200,)),
                    {"builds": [{"url": JOB + "11/"}, {"queueId": 42, "url": BUILD}]},
                    queue_test.e2e.JenkinsError(BUILD_API, 404, (200,)),
                    {"building": True},
                    {"building": True},
                    {"building": False},
                ]
                queue_test.settle_build(admin, JOB, QUEUE)
                self.assertEqual(admin.call.call_args_list, [
                    call(CANCEL, {}, expected=(204,)),
                    call(BUILD + "stop", {}, expected=(200, 302, 303, 404)),
                ])
                self.assertEqual(admin.json.call_args_list, [
                    call(QUEUE + "api/json"), call(BUILDS_API),
                    call(QUEUE + "api/json"), call(BUILDS_API),
                    call(BUILD_API), call(BUILD_API), call(BUILD_API), call(BUILD_API),
                ])

    def test_confirmed_cancel_checks_history(self):
        admin = Mock()
        admin.json.side_effect = [{"blocked": True}, {"builds": []}]
        queue_test.settle_build(admin, JOB, QUEUE)
        self.assertEqual(admin.json.call_args_list, [call(QUEUE + "api/json"), call(BUILDS_API)])
        admin.call.assert_called_once_with(CANCEL, {}, expected=(204,))

    def test_history_404_after_cancel_is_retried_without_cancelling_again(self):
        admin = Mock()
        admin.json.side_effect = [
            {"blocked": True},
            queue_test.e2e.JenkinsError(BUILDS_API, 404, (200,)),
            {"blocked": True},
            {"builds": [{"queueId": 42, "url": BUILD}]},
            {"building": True},
            {"building": False},
        ]
        with patch.object(queue_test.e2e.time, "sleep"):
            queue_test.settle_build(admin, JOB, QUEUE)
        self.assertEqual(admin.call.call_args_list, [
            call(CANCEL, {}, expected=(204,)),
            call(BUILD + "stop", {}, expected=(200, 302, 303, 404)),
        ])

    def test_executable_takes_precedence_when_queue_reports_cancelled(self):
        admin = Mock()
        admin.json.side_effect = [
            {"cancelled": True, "executable": {"url": BUILD}},
            {"building": True},
            {"building": False},
        ]
        queue_test.settle_build(admin, JOB, QUEUE)
        admin.call.assert_called_once_with(BUILD + "stop", {}, expected=(200, 302, 303, 404))

    def test_cancelled_queue_item_checks_history_without_posting(self):
        admin = Mock()
        admin.json.side_effect = [{"cancelled": True}, {"builds": []}]
        queue_test.settle_build(admin, JOB, QUEUE)
        self.assertEqual(admin.json.call_args_list, [call(QUEUE + "api/json"), call(BUILDS_API)])
        admin.call.assert_not_called()

    def test_stops_known_build_without_queue_path(self):
        admin = Mock()
        admin.json.side_effect = [{"building": True}, {"building": False}]
        with patch.object(queue_test.e2e.time, "sleep"):
            queue_test.settle_build(admin, JOB, None, BUILD)
        self.assertEqual(admin.json.call_args_list, [call(BUILD_API), call(BUILD_API)])
        admin.call.assert_called_once_with(BUILD + "stop", {}, expected=(200, 302, 303, 404))

    def test_stop_404_still_waits_for_terminal_state(self):
        admin = Mock()
        admin.call.return_value = (b"", {})
        admin.json.side_effect = [{"building": True}, {"building": True}, {"building": False}]
        with patch.object(queue_test.e2e.time, "sleep"):
            queue_test.settle_build(admin, JOB, QUEUE, BUILD)
        self.assertEqual(admin.json.call_args_list, [call(BUILD_API)] * 3)
        admin.call.assert_called_once_with(BUILD + "stop", {}, expected=(200, 302, 303, 404))

    def test_missing_queue_and_build_expires_with_diagnostic(self):
        admin = Mock()
        admin.json.side_effect = [
            queue_test.e2e.JenkinsError(QUEUE, 404, (200,)),
            {"builds": []},
        ]
        with patch.object(queue_test.e2e.time, "sleep"), patch.object(
            queue_test.e2e.time, "monotonic", side_effect=count(0, 31)
        ), self.assertRaisesRegex(TimeoutError, "Could not settle J4 queue item 42"):
            queue_test.settle_build(admin, JOB, QUEUE)


if __name__ == "__main__":
    unittest.main()
