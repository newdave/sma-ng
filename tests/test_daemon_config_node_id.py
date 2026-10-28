"""Only the daemon persists a generated node_id into sma-ng.yml."""

from resources.daemon.config import PathConfigManager

CONFIG = "daemon:\n  api-key: k\n"


def test_daemon_persists_generated_node_id(tmp_path):
  cfg = tmp_path / "sma-ng.yml"
  cfg.write_text(CONFIG)
  pcm = PathConfigManager(str(cfg))
  assert pcm.node_id and "node_id: %s" % pcm.node_id in cfg.read_text()


def test_tools_leave_config_untouched(tmp_path):
  cfg = tmp_path / "sma-ng.yml"
  cfg.write_text(CONFIG)
  before = cfg.stat().st_mtime_ns
  pcm = PathConfigManager(str(cfg), persist_node_id=False)
  assert pcm.node_id
  assert cfg.read_text() == CONFIG and cfg.stat().st_mtime_ns == before
