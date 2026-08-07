from server.downsample.resolution import select_resolution


def test_resolution_selection():
    assert select_resolution(19 * 365 * 86400, 2000).name == "1d"
    assert select_resolution(365 * 86400, 2000).name == "1h"
    assert select_resolution(86400, 2000).name == "1m"
    r = select_resolution(3600, 2000)
    assert r.is_raw
    assert not select_resolution(30 * 86400, 8000).is_raw
