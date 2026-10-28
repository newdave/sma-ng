"""Tests for resources/cli/plan.py (plan protocol v1)."""

import pytest

from resources.cli.errors import PlanError
from resources.cli.plan import ALLOWED_SUBCOMMANDS, PLAN_HEADER, Plan, Step


def test_serialize_keeps_item_order_and_header():
  plan = Plan()
  plan.step("run", "--rm").optmount("/transcode").arg("img", "run", "python", "repair.py", "/a")
  plan.step("pull", "img")
  assert plan.serialize().splitlines() == [
    PLAN_HEADER,
    "step docker",
    "arg run",
    "arg --rm",
    "optmount /transcode",
    "arg img",
    "arg run",
    "arg python",
    "arg repair.py",
    "arg /a",
    "end",
    "step docker",
    "arg pull",
    "arg img",
    "end",
  ]
  assert PLAN_HEADER == "sma-plan 1"


@pytest.mark.parametrize("value", ["My Show (2020)/ep 1.mkv", 'it\'s "quoted"', "*.mkv", "-n", "$(rm -rf /)", "a;b", ""])
def test_values_preserved_verbatim(value):
  plan = Plan()
  plan.step("run", value)
  assert "arg %s" % value in plan.serialize().splitlines()


@pytest.mark.parametrize("value", ["a\nb", "a\rb", "a\0b"])
def test_newline_and_nul_rejected(value):
  with pytest.raises(PlanError):
    Plan().step("run", value)
  with pytest.raises(PlanError):
    Step().optmount(value)


def test_non_string_rejected():
  with pytest.raises(PlanError):
    Step().arg(5)


def test_subcommand_allow_list():
  for sub in ALLOWED_SUBCOMMANDS:
    Plan().step(sub)
  with pytest.raises(PlanError, match="not allowed"):
    Plan().step("system", "prune")


def test_step_must_start_with_subcommand():
  plan = Plan()
  plan.steps.append(Step().optmount("/x"))
  with pytest.raises(PlanError, match="must start"):
    plan.serialize()
  with pytest.raises(PlanError):
    Step().validate()


def test_plan_error_exit_code():
  assert PlanError.exit_code == 5
