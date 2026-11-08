# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
import os
import sys
import types
import unittest
from unittest import mock

from mirto.cameras import permissions


def fake_av(status, grant=True):
    av = types.ModuleType("AVFoundation")
    av.AVMediaTypeVideo = "vide"
    calls = []

    def request(media, handler):
        calls.append(media)
        handler(grant)

    av.AVCaptureDevice = types.SimpleNamespace(
        authorizationStatusForMediaType_=lambda m: status,
        requestAccessForMediaType_completionHandler_=request,
    )
    return av, calls


class PermissionTest(unittest.TestCase):
    def run_with(self, status, grant=True):
        av, calls = fake_av(status, grant)
        with mock.patch.object(permissions, "sistema", lambda: "darwin"), \
                mock.patch.dict(sys.modules, {"AVFoundation": av}), \
                mock.patch.object(permissions.time, "sleep"):
            return permissions.ensure_camera_permission(timeout=2), calls

    def test_authorized(self):
        (ok, _msg), calls = self.run_with(permissions.AUTHORIZED)
        self.assertTrue(ok)
        self.assertEqual(calls, [])
        self.assertEqual(os.environ.get("OPENCV_AVFOUNDATION_SKIP_AUTH"), "1")

    def test_not_determined_waits_for_answer(self):
        (ok, msg), calls = self.run_with(permissions.NOT_DETERMINED, grant=True)
        self.assertTrue(ok)
        self.assertEqual(calls, ["vide"])

    def test_denied(self):
        (ok, msg), _ = self.run_with(permissions.DENIED)
        self.assertFalse(ok)
        self.assertIn("Fotocamera", msg)

    def test_user_refuses(self):
        (ok, msg), _ = self.run_with(permissions.NOT_DETERMINED, grant=False)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
