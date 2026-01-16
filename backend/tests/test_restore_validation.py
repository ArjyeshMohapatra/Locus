import os
from backend.app.main import _is_within_watched_paths, _normalize_path


def test_normalize_path_and_within_true():
    # watched path is backend folder
    watched = [os.path.abspath(os.path.join(os.getcwd(), "backend"))]
    target = os.path.join(watched[0], "sub", "file.txt")
    assert _is_within_watched_paths(target, watched)


def test_within_false_outside():
    watched = [os.path.abspath(os.path.join(os.getcwd(), "backend"))]
    # a path clearly outside
    target = os.path.abspath(
        os.path.join(os.path.dirname(watched[0]), "other_folder", "f.txt")
    )
    assert not _is_within_watched_paths(target, watched)


def test_within_false_traversal_attempt():
    watched = [os.path.abspath(os.path.join(os.getcwd(), "backend"))]
    # simulate an attempt to traverse out
    target = os.path.abspath(
        os.path.join(watched[0], "..", "..", "Windows", "system32", "hosts")
    )
    assert not _is_within_watched_paths(target, watched)
