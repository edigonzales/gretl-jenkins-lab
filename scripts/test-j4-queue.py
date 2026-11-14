#!/usr/bin/env python3
"""Regression tests for cleanup during a Jenkins queue handoff."""
import importlib.util
import unittest
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
                    call(BUILD + "stop", {}, expected=(200, 302, 303)),
                ])
                self.assertEqual(admin.json.call_args_list, [
                    call(QUEUE + "api/json"), call(BUILDS_API),
                    call(QUEUE + "api/json"), call(BUILDS_API),
                    call(BUILD_API), call(BUILD_API), call(BUILD_API), call(BUILD_API),
                ])

    def test_confirmed_cancel_finishes_without_looking_for_a_build(self):
        admin = Mock()
        admin.json.return_value = {"blocked": True}
        queue_test.settle_build(admin, JOB, QUEUE)
        admin.json.assert_called_once_with(QUEUE + "api/json")
        admin.call.assert_called_once_with(CANCEL, {}, expected=(204,))


if __name__ == "__main__":
    unittest.main()
