"""T029 — scoped deletion (load-bearing, Constitution P5). FR-024, FR-025."""

import pytest

from ankivoice.cleanup import remove_job_dir


def test_removes_job_dir_inside_work_root(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    job = work / "job_1"
    job.mkdir()
    (job / "in.txt").write_text("x")
    (job / "a.mp3").write_bytes(b"\x00")

    remove_job_dir(job, work_root=work)

    assert not job.exists()
    assert work.exists()  # only the job dir was removed


def test_refuses_target_outside_work_root(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("important")

    with pytest.raises(ValueError):
        remove_job_dir(outside, work_root=work)
    assert (outside / "keep.txt").exists()


def test_refuses_work_root_itself(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(ValueError):
        remove_job_dir(work, work_root=work)
    assert work.exists()


def test_refuses_parent_traversal(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "evil").mkdir()
    (tmp_path / "evil" / "keep.txt").write_text("important")

    with pytest.raises(ValueError):
        remove_job_dir(work / ".." / "evil", work_root=work)
    assert (tmp_path / "evil" / "keep.txt").exists()


def test_refuses_symlink_escape(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "s.txt").write_text("secret")
    link = work / "job_link"
    link.symlink_to(secret, target_is_directory=True)

    with pytest.raises(ValueError):
        remove_job_dir(link, work_root=work)
    assert (secret / "s.txt").exists()


def test_idempotent_when_missing(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    remove_job_dir(work / "job_gone", work_root=work)  # no error
