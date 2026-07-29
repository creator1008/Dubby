"""Run ``demucs.separate`` with soundfile-backed torchaudio I/O.

TorchAudio 2.9+ routes ``save``/``load`` through TorchCodec, which needs
FFmpeg shared libraries that are often missing on Windows. Demucs only
needs WAV I/O here, so we swap in soundfile before launching the CLI.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torchaudio


def _to_numpy(src: torch.Tensor) -> np.ndarray:
    data = src.detach().cpu().numpy()
    if data.ndim == 2:
        # torchaudio convention: (channels, time) -> soundfile (time, channels)
        data = data.T
    return data


def _save(
    uri,
    src,
    sample_rate: int,
    channels_first: bool = True,
    **_kwargs,
) -> None:
    path = Path(str(uri))
    path.parent.mkdir(parents=True, exist_ok=True)
    tensor = src if isinstance(src, torch.Tensor) else torch.as_tensor(src)
    if not channels_first and tensor.ndim == 2:
        tensor = tensor.transpose(0, 1)
    sf.write(str(path), _to_numpy(tensor), int(sample_rate))


def _load(
    uri,
    frame_offset: int = 0,
    num_frames: int = -1,
    normalize: bool = True,
    channels_first: bool = True,
    **_kwargs,
):
    data, sample_rate = sf.read(
        str(uri),
        start=frame_offset if frame_offset > 0 else 0,
        frames=num_frames if num_frames >= 0 else -1,
        dtype="float32",
        always_2d=True,
    )
    tensor = torch.from_numpy(data.T.copy() if channels_first else data.copy())
    if normalize is False:
        # soundfile float32 is already [-1, 1]; keep API compatibility.
        pass
    return tensor, int(sample_rate)


def _install_soundfile_io() -> None:
    torchaudio.save = _save  # type: ignore[assignment]
    torchaudio.load = _load  # type: ignore[assignment]


def main(argv: list[str] | None = None) -> None:
    _install_soundfile_io()
    from demucs.separate import main as demucs_main

    demucs_main(argv)


if __name__ == "__main__":
    main()
