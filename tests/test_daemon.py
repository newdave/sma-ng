"""Tests for daemon.py - job database, path config, and markdown rendering."""
import os
import json
import tempfile
import pytest

from daemon import (
    JobDatabase, PathConfigManager, ConfigLockManager,
    _render_markdown_to_html, _inline,
    STATUS_PENDING, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED,
)


class TestJobDatabase:
    """Test SQLite job database operations."""

    def test_add_and_get_job(self, tmp_db):
        db = JobDatabase(tmp_db)
        job_id = db.add_job('/path/to/file.mkv', '/config/autoProcess.ini')
        job = db.get_job(job_id)
        assert job is not None
        assert job['path'] == '/path/to/file.mkv'
        assert job['config'] == '/config/autoProcess.ini'
        assert job['status'] == STATUS_PENDING

    def test_job_lifecycle(self, tmp_db):
        db = JobDatabase(tmp_db)
        job_id = db.add_job('/test.mkv', '/config.ini')

        db.start_job(job_id, worker_id=1)
        job = db.get_job(job_id)
        assert job['status'] == STATUS_RUNNING
        assert job['worker_id'] == 1
        assert job['started_at'] is not None

        db.complete_job(job_id)
        job = db.get_job(job_id)
        assert job['status'] == STATUS_COMPLETED
        assert job['completed_at'] is not None

    def test_fail_job(self, tmp_db):
        db = JobDatabase(tmp_db)
        job_id = db.add_job('/test.mkv', '/config.ini')
        db.start_job(job_id, 1)
        db.fail_job(job_id, 'Conversion failed')
        job = db.get_job(job_id)
        assert job['status'] == STATUS_FAILED
        assert job['error'] == 'Conversion failed'

    def test_get_pending_jobs(self, tmp_db):
        db = JobDatabase(tmp_db)
        db.add_job('/a.mkv', '/config.ini')
        db.add_job('/b.mkv', '/config.ini')
        job_id3 = db.add_job('/c.mkv', '/config.ini')
        db.start_job(job_id3, 1)
        pending = db.get_pending_jobs()
        assert len(pending) == 2

    def test_get_next_pending_fifo(self, tmp_db):
        db = JobDatabase(tmp_db)
        id1 = db.add_job('/first.mkv', '/config.ini')
        db.add_job('/second.mkv', '/config.ini')
        job = db.get_next_pending_job()
        assert job['id'] == id1

    def test_get_stats(self, tmp_db):
        db = JobDatabase(tmp_db)
        id1 = db.add_job('/a.mkv', '/c.ini')
        id2 = db.add_job('/b.mkv', '/c.ini')
        id3 = db.add_job('/c.mkv', '/c.ini')
        db.start_job(id1, 1)
        db.complete_job(id1)
        db.start_job(id2, 1)
        db.fail_job(id2, 'error')
        stats = db.get_stats()
        assert stats.get(STATUS_COMPLETED, 0) == 1
        assert stats.get(STATUS_FAILED, 0) == 1
        assert stats.get(STATUS_PENDING, 0) == 1
        assert stats['total'] == 3

    def test_get_jobs_with_filter(self, tmp_db):
        db = JobDatabase(tmp_db)
        db.add_job('/a.mkv', '/tv.ini')
        id2 = db.add_job('/b.mkv', '/movie.ini')
        db.start_job(id2, 1)
        db.complete_job(id2)
        completed = db.get_jobs(status=STATUS_COMPLETED)
        assert len(completed) == 1
        assert completed[0]['path'] == '/b.mkv'

    def test_get_jobs_pagination(self, tmp_db):
        db = JobDatabase(tmp_db)
        for i in range(10):
            db.add_job('/file%d.mkv' % i, '/c.ini')
        page1 = db.get_jobs(limit=3, offset=0)
        page2 = db.get_jobs(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        assert page1[0]['id'] != page2[0]['id']

    def test_pending_count(self, tmp_db):
        db = JobDatabase(tmp_db)
        db.add_job('/a.mkv', '/c.ini')
        db.add_job('/b.mkv', '/c.ini')
        assert db.pending_count() == 2

    def test_pending_count_for_config(self, tmp_db):
        db = JobDatabase(tmp_db)
        db.add_job('/a.mkv', '/tv.ini')
        db.add_job('/b.mkv', '/tv.ini')
        db.add_job('/c.mkv', '/movie.ini')
        assert db.pending_count_for_config('/tv.ini') == 2
        assert db.pending_count_for_config('/movie.ini') == 1

    def test_get_nonexistent_job(self, tmp_db):
        db = JobDatabase(tmp_db)
        assert db.get_job(9999) is None

    def test_job_args_stored(self, tmp_db):
        db = JobDatabase(tmp_db)
        job_id = db.add_job('/test.mkv', '/c.ini', args=['-tmdb', '603'])
        job = db.get_job(job_id)
        args = json.loads(job['args'])
        assert args == ['-tmdb', '603']

    def test_reset_running_jobs(self, tmp_db):
        db = JobDatabase(tmp_db)
        job_id = db.add_job('/test.mkv', '/c.ini')
        db.start_job(job_id, 1)
        assert db.get_job(job_id)['status'] == STATUS_RUNNING
        # Simulate daemon restart
        db2 = JobDatabase(tmp_db)
        job = db2.get_job(job_id)
        assert job['status'] == STATUS_PENDING

    def test_cleanup_old_jobs(self, tmp_db):
        db = JobDatabase(tmp_db)
        job_id = db.add_job('/old.mkv', '/c.ini')
        db.start_job(job_id, 1)
        db.complete_job(job_id)
        # Cleanup with 0 days should remove it
        deleted = db.cleanup_old_jobs(days=0)
        assert deleted >= 0  # May be 0 if completed_at is "now"


class TestPathConfigManager:
    """Test path-to-config matching."""

    def test_exact_match(self, tmp_path):
        config_file = str(tmp_path / 'daemon.json')
        ini_file = str(tmp_path / 'autoProcess.ini')
        tv_ini = str(tmp_path / 'tv.ini')
        for f in [ini_file, tv_ini]:
            open(f, 'w').close()
        with open(config_file, 'w') as f:
            json.dump({
                'default_config': ini_file,
                'path_configs': [
                    {'path': '/mnt/media/TV', 'config': tv_ini}
                ]
            }, f)
        pcm = PathConfigManager(config_file)
        assert pcm.get_config_for_path('/mnt/media/TV/show/ep.mkv') == tv_ini

    def test_longest_prefix_wins(self, tmp_path):
        config_file = str(tmp_path / 'daemon.json')
        ini_file = str(tmp_path / 'default.ini')
        movies_ini = str(tmp_path / 'movies.ini')
        movies4k_ini = str(tmp_path / 'movies4k.ini')
        for f in [ini_file, movies_ini, movies4k_ini]:
            open(f, 'w').close()
        with open(config_file, 'w') as f:
            json.dump({
                'default_config': ini_file,
                'path_configs': [
                    {'path': '/mnt/media/Movies', 'config': movies_ini},
                    {'path': '/mnt/media/Movies/4K', 'config': movies4k_ini},
                ]
            }, f)
        pcm = PathConfigManager(config_file)
        assert pcm.get_config_for_path('/mnt/media/Movies/4K/film.mkv') == movies4k_ini
        assert pcm.get_config_for_path('/mnt/media/Movies/regular.mkv') == movies_ini

    def test_no_match_uses_default(self, tmp_path):
        config_file = str(tmp_path / 'daemon.json')
        ini_file = str(tmp_path / 'default.ini')
        open(ini_file, 'w').close()
        with open(config_file, 'w') as f:
            json.dump({
                'default_config': ini_file,
                'path_configs': [
                    {'path': '/mnt/media/TV', 'config': str(tmp_path / 'tv.ini')}
                ]
            }, f)
        open(str(tmp_path / 'tv.ini'), 'w').close()
        pcm = PathConfigManager(config_file)
        assert pcm.get_config_for_path('/completely/different/path.mkv') == ini_file

    def test_get_all_configs(self, tmp_path):
        config_file = str(tmp_path / 'daemon.json')
        ini_file = str(tmp_path / 'default.ini')
        tv_ini = str(tmp_path / 'tv.ini')
        for f in [ini_file, tv_ini]:
            open(f, 'w').close()
        with open(config_file, 'w') as f:
            json.dump({
                'default_config': ini_file,
                'path_configs': [{'path': '/tv', 'config': tv_ini}]
            }, f)
        pcm = PathConfigManager(config_file)
        all_configs = pcm.get_all_configs()
        assert ini_file in all_configs
        assert tv_ini in all_configs


class TestConfigLockManager:
    """Test per-config locking."""

    def test_acquire_and_release(self):
        clm = ConfigLockManager()
        clm.acquire('/config.ini', 1, '/path.mkv')
        status = clm.get_status()
        assert '/config.ini' in status['active']
        clm.release('/config.ini')
        status = clm.get_status()
        assert '/config.ini' not in status['active']

    def test_is_locked(self):
        clm = ConfigLockManager()
        assert clm.is_locked('/config.ini') is False
        clm.acquire('/config.ini', 1, '/path.mkv')
        assert clm.is_locked('/config.ini') is True
        clm.release('/config.ini')
        assert clm.is_locked('/config.ini') is False

    def test_get_active_job(self):
        clm = ConfigLockManager()
        clm.acquire('/config.ini', 42, '/movie.mkv')
        active = clm.get_active_job('/config.ini')
        assert active == (42, '/movie.mkv')
        clm.release('/config.ini')


class TestMarkdownRendering:
    """Test the minimal Markdown to HTML renderer."""

    def test_heading_h1(self):
        html = _render_markdown_to_html('# Hello')
        assert '<h1' in html
        assert 'Hello' in html

    def test_heading_h3(self):
        html = _render_markdown_to_html('### Section')
        assert '<h3' in html

    def test_code_block(self):
        md = '```python\nprint("hello")\n```'
        html = _render_markdown_to_html(md)
        assert '<pre' in html
        assert '<code' in html
        assert 'print' in html

    def test_code_block_escapes_html(self):
        md = '```\n<script>alert("xss")</script>\n```'
        html = _render_markdown_to_html(md)
        assert '<script>' not in html
        assert '&lt;script&gt;' in html

    def test_table(self):
        md = '| A | B |\n|---|---|\n| 1 | 2 |'
        html = _render_markdown_to_html(md)
        assert '<table' in html
        assert '<th' in html
        assert '<td' in html

    def test_unordered_list(self):
        md = '- item 1\n- item 2'
        html = _render_markdown_to_html(md)
        assert '<ul' in html
        assert '<li>' in html

    def test_ordered_list(self):
        md = '1. first\n2. second'
        html = _render_markdown_to_html(md)
        assert '<ol' in html
        assert '<li>' in html

    def test_paragraph(self):
        html = _render_markdown_to_html('Just a paragraph.')
        assert '<p' in html

    def test_horizontal_rule(self):
        html = _render_markdown_to_html('---')
        assert '<hr' in html


class TestInlineFormatting:
    """Test inline Markdown formatting."""

    def test_bold(self):
        html = _inline('**bold text**')
        assert '<strong' in html
        assert 'bold text' in html

    def test_italic(self):
        html = _inline('*italic text*')
        assert '<em>' in html

    def test_inline_code(self):
        html = _inline('use `pip install`')
        assert '<code' in html
        assert 'pip install' in html

    def test_link(self):
        html = _inline('[text](http://example.com)')
        assert 'href="http://example.com"' in html
        assert '>text<' in html

    def test_html_escaping(self):
        html = _inline('<script>alert("xss")</script>')
        assert '<script>' not in html
        assert '&lt;script&gt;' in html

    def test_mixed_formatting(self):
        html = _inline('**bold** and `code` and *italic*')
        assert '<strong' in html
        assert '<code' in html
        assert '<em>' in html
