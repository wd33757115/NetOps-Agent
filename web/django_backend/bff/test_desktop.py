import json
from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from rest_framework_simplejwt.tokens import AccessToken

from .models import DesktopPreference
from .views.desktop import default_layout, desktop_layout, validate_layout


class DesktopPreferenceTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(username="desktop-one")
        self.other = get_user_model().objects.create_user(username="desktop-two")

    def request(self, method="get", body=None, user=None, authenticated=True):
        request = getattr(self.factory, method)("/api/auth/me/desktop-layout/", data=json.dumps(body) if body else "", content_type="application/json")
        if authenticated:
            request.META["HTTP_AUTHORIZATION"] = "Bearer " + str(AccessToken.for_user(user or self.user))
        with patch("bff.decorators.token_is_revoked", return_value=False):
            return desktop_layout(request)

    def test_defaults_do_not_create_a_record(self):
        response = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["data"]["revision"], 0)
        self.assertFalse(DesktopPreference.objects.exists())

    def test_save_conflict_and_user_isolation(self):
        body = {**default_layout(), "revision": 0, "userId": self.other.id}
        body["nodes"][0]["row"] = 2
        response = self.request("put", body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.request("put", body).status_code, 409)
        self.assertEqual(json.loads(self.request().content)["data"]["nodes"][0]["row"], 2)
        self.assertEqual(json.loads(self.request(user=self.other).content)["data"]["nodes"][0]["row"], 0)
        body["revision"] = 1
        self.assertEqual(self.request("put", body).status_code, 200)
        self.assertEqual(DesktopPreference.objects.get(user=self.user).revision, 2)

    def test_authentication_required_even_in_development(self):
        self.assertEqual(self.request(authenticated=False).status_code, 401)

    def test_layout_validation(self):
        original = {**default_layout(), "revision": 0}
        for mutate in [
            lambda v: v["nodes"].append(deepcopy(v["nodes"][0])),
            lambda v: v["nodes"][0].update(containerId="folder:missing", order=0),
            lambda v: v["nodes"][0].update(col=-1),
            lambda v: v["nodes"][0].update(col=True),
            lambda v: v["nodes"][1].update(col=0),
            lambda v: v.update(dockAppIds=["missing"]),
            lambda v: v.update(revision=-1),
            lambda v: v.update(schemaVersion=2),
        ]:
            body = deepcopy(original)
            mutate(body)
            with self.assertRaises(ValueError):
                validate_layout(body)
        self.assertEqual(self.request("put", {"nodes": []}).status_code, 400)

    def test_folder_and_unknown_app_references_survive_round_trip(self):
        body = {**default_layout(), "revision": 0}
        body["nodes"].append({"id": "folder-1", "kind": "folder", "name": "网络运维", "containerId": "desktop:0", "col": 2, "row": 0})
        body["nodes"][0].update(containerId="folder:folder-1", order=0)
        body["nodes"].append({"id": "future", "kind": "app", "appId": "future-app", "containerId": "desktop:0", "col": 3, "row": 0})
        self.assertEqual(self.request("put", body).status_code, 200)
        saved = json.loads(self.request().content)["data"]
        self.assertEqual(saved["nodes"][0]["containerId"], "folder:folder-1")
        self.assertNotIn("col", saved["nodes"][0])
        self.assertEqual(saved["nodes"][-1]["appId"], "future-app")

    def test_wallpaper_is_private_and_survives_old_client_layout_update(self):
        body = {**default_layout(), "revision": 0, "wallpaper": {"type": "preset", "id": "forest"}}
        self.assertEqual(self.request("put", body).status_code, 200)
        self.assertEqual(json.loads(self.request().content)["data"]["wallpaper"], body["wallpaper"])
        self.assertNotIn("wallpaper", json.loads(self.request(user=self.other).content)["data"])
        self.assertEqual(self.request("put", {**default_layout(), "revision": 1}).status_code, 200)
        self.assertEqual(json.loads(self.request().content)["data"]["wallpaper"], body["wallpaper"])
        self.assertEqual(self.request("put", body).status_code, 409)

    def test_wallpaper_validation_rejects_external_urls_svg_and_oversized_images(self):
        for wallpaper in [None, {"type": "preset", "id": "unknown"}, {"type": "image", "dataUrl": "https://example.com/tracker.jpg"},
                          {"type": "image", "dataUrl": "data:image/svg+xml;base64,PHN2Zz4="},
                          {"type": "image", "dataUrl": "data:image/jpeg;base64,bm90YW5pbWFnZQ=="},
                          {"type": "image", "dataUrl": "data:image/jpeg;base64," + "A" * 524288}]:
            response = self.request("put", {**default_layout(), "revision": 0, "wallpaper": wallpaper})
            self.assertEqual(response.status_code, 400)
        self.assertFalse(DesktopPreference.objects.exists())

    def test_encoded_wallpaper_round_trip_and_oversized_request(self):
        import base64
        encoded = "data:image/jpeg;base64," + base64.b64encode(bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")).decode()
        body = {**default_layout(), "revision": 0, "wallpaper": {"type": "image", "dataUrl": encoded}}
        self.assertEqual(self.request("put", body).status_code, 200)
        self.assertEqual(json.loads(self.request().content)["data"]["wallpaper"]["dataUrl"], encoded)
        self.assertEqual(self.request("put", {**body, "padding": "x" * 614400}).status_code, 413)
