import numpy as np

from jarvis.voice import resample_to_16k


def test_resample_48k_to_16k_length_and_passthrough():
    x = np.sin(np.linspace(0, 100, 4800)).astype(np.float32)
    assert len(resample_to_16k(x, 48000)) == 1600
    assert resample_to_16k(x, 16000) is x


def test_resample_44k_block_gives_one_wakeword_frame():
    x = np.zeros(3528, dtype=np.float32)          # 80 ms at 44.1 kHz, this laptop's mic
    y = resample_to_16k(x, 44100)
    assert len(y) == 1280 and y.dtype == np.float32
