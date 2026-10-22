import numpy as np

from jarvis.voice import resample_to_16k


def test_resample_48k_to_16k_length_and_passthrough():
    x = np.sin(np.linspace(0, 100, 4800)).astype(np.float32)
    assert len(resample_to_16k(x, 48000)) == 1600
    assert resample_to_16k(x, 16000) is x


def test_resample_filters_above_8k_instead_of_aliasing():
    t = np.arange(44100) / 44100
    y = resample_to_16k(np.sin(2 * np.pi * 10000 * t).astype(np.float32), 44100)
    assert np.abs(y[2000:-2000]).max() < 0.05   # linear interpolation folds it to 6 kHz


def test_resample_44k_block_gives_one_wakeword_frame():
    x = np.zeros(3528, dtype=np.float32)          # 80 ms at 44.1 kHz, this laptop's mic
    y = resample_to_16k(x, 44100)
    assert len(y) == 1280 and y.dtype == np.float32
