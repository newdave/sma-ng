from resources.daemon.config import _strip_secrets


def test_strips_kebab_case_daemon_secrets():
  data = {"daemon": {"api-key": "a", "db-url": "postgres://u:p@h/d", "node-id": "n", "username": "u", "password": "p", "workers": 2}}
  assert _strip_secrets(data)["daemon"] == {"workers": 2}


def test_strips_snake_case_daemon_secrets():
  data = {"daemon": {"api_key": "a", "db_url": "x", "node_id": "n"}}
  assert _strip_secrets(data)["daemon"] == {}


def test_strips_service_secrets_kebab_and_plain():
  data = {"services": {"sonarr": {"main": {"apikey": "k", "token": "t", "url": "http://x"}}}}
  assert _strip_secrets(data)["services"]["sonarr"]["main"] == {"url": "http://x"}


def test_redacts_subliminal_auth():
  data = {"base": {"subtitle": {"subliminal": {"auth": {"opensubtitles": "user:pw", "tvsubtitles": ""}}}}}
  auth = _strip_secrets(data)["base"]["subtitle"]["subliminal"]["auth"]
  assert auth["opensubtitles"] == "***"
  assert auth["tvsubtitles"] == ""
  assert data["base"]["subtitle"]["subliminal"]["auth"]["opensubtitles"] == "user:pw"
