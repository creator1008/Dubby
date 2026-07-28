"""Tests for local pipeline R2 key helpers."""

from app.local_r2_store import LOCAL_RUN_PREFIX, LocalR2Store


def test_object_key_normalizes_slashes() -> None:
    store = LocalR2Store.__new__(LocalR2Store)
    assert (
        store.object_key("abc123", "speech/0001.wav")
        == f"{LOCAL_RUN_PREFIX}/abc123/speech/0001.wav"
    )


def test_retain_final_videos_keeps_source_and_output_only() -> None:
    store = LocalR2Store.__new__(LocalR2Store)
    deleted: list[str] = []

    class FakePaginator:
        def paginate(self, **kwargs):  # noqa: ANN003
            prefix = kwargs["Prefix"]
            yield {
                "Contents": [
                    {"Key": f"{prefix}source.mp4"},
                    {"Key": f"{prefix}dubbed_output.mp4"},
                    {"Key": f"{prefix}original_audio.wav"},
                    {"Key": f"{prefix}speech/0001.wav"},
                    {"Key": f"{prefix}manifest.json"},
                ]
            }

    class FakeClient:
        def get_paginator(self, name: str):  # noqa: ARG002
            return FakePaginator()

        def delete_objects(self, **kwargs):  # noqa: ANN003
            deleted.extend(obj["Key"] for obj in kwargs["Delete"]["Objects"])

    store._settings = type("S", (), {"r2_bucket": "dubby"})()  # type: ignore[attr-defined]
    store._client = FakeClient()  # type: ignore[attr-defined]
    store.retain_final_videos("run1", "source.mp4")
    assert set(deleted) == {
        f"{LOCAL_RUN_PREFIX}/run1/original_audio.wav",
        f"{LOCAL_RUN_PREFIX}/run1/speech/0001.wav",
        f"{LOCAL_RUN_PREFIX}/run1/manifest.json",
    }
