"""Analyzer contracts and pure recommendation policy for per-job transcoder tuning."""

from dataclasses import dataclass, field
from typing import Any

DEFAULT_ANALYZER_POLICY = {
    "enabled": True,
    "allow_codec_reorder": True,
    "allow_bitrate_adjustments": True,
    "allow_preset_adjustments": True,
    "allow_filter_adjustments": True,
    "allow_force_reencode": True,
}


@dataclass(slots=True)
class AnalyzerObservations:
    """Normalized observations emitted by a deterministic or ML-backed analyzer."""

    content_type: str = "general_live_action"
    noise_score: float = 0.0
    motion_score: float = 0.0
    interlace_confidence: float = 0.0
    crop_confidence: float = 0.0
    crop_filter: str | None = None


@dataclass(slots=True)
class AnalyzerRecommendations:
    """Bounded planner overrides derived from analyzer observations."""

    codec_order: list[str] = field(default_factory=list)
    bitrate_ratio_multiplier: float | None = None
    max_bitrate_ceiling: int | None = None
    preset: str | None = None
    filters: list[str] = field(default_factory=list)
    force_reencode: bool = False
    reasons: list[str] = field(default_factory=list)


def _merged_policy_config(analyzer_config: dict[str, Any] | None) -> dict[str, Any]:
    config = dict(DEFAULT_ANALYZER_POLICY)
    if analyzer_config:
        config.update(analyzer_config)
    return config


def _append_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _set_bitrate_multiplier(recommendations: AnalyzerRecommendations, multiplier: float) -> None:
    current = recommendations.bitrate_ratio_multiplier
    if current is None:
        recommendations.bitrate_ratio_multiplier = multiplier
    else:
        recommendations.bitrate_ratio_multiplier = max(current, multiplier)


def build_recommendations(observations: AnalyzerObservations, analyzer_config: dict[str, Any] | None = None) -> AnalyzerRecommendations:
    """Translate analyzer observations into bounded per-job recommendations."""

    config = _merged_policy_config(analyzer_config)
    recommendations = AnalyzerRecommendations()

    if not config.get("enabled", True):
        return recommendations

    if observations.content_type == "animation":
        if config.get("allow_codec_reorder", True):
            recommendations.codec_order = ["av1", "h265", "h264"]
            recommendations.reasons.append("animation content benefits from denser codecs")
        if config.get("allow_bitrate_adjustments", True):
            recommendations.bitrate_ratio_multiplier = 0.9
            recommendations.max_bitrate_ceiling = 8000
            recommendations.reasons.append("animation content can usually run at a lower bitrate")
    elif observations.content_type == "talking_head":
        if config.get("allow_bitrate_adjustments", True):
            recommendations.bitrate_ratio_multiplier = 0.85
            recommendations.max_bitrate_ceiling = 4000
            recommendations.reasons.append("talking-head content is typically compressible")
        if config.get("allow_preset_adjustments", True):
            recommendations.preset = "slow"
            recommendations.reasons.append("talking-head content benefits from a slower preset")
    elif observations.content_type == "sports_high_motion" or observations.motion_score >= 0.8:
        if config.get("allow_bitrate_adjustments", True):
            recommendations.bitrate_ratio_multiplier = 1.15
            recommendations.max_bitrate_ceiling = 12000
            recommendations.reasons.append("high-motion content needs more bitrate headroom")
        if config.get("allow_preset_adjustments", True):
            recommendations.preset = "fast"
            recommendations.reasons.append("high-motion content benefits from a faster preset")

    if observations.noise_score >= 0.7:
        if config.get("allow_filter_adjustments", True):
            _append_once(recommendations.filters, "hqdn3d")
            recommendations.reasons.append("noisy content benefits from denoise filtering")
        if config.get("allow_bitrate_adjustments", True):
            _set_bitrate_multiplier(recommendations, 1.1)
            if recommendations.max_bitrate_ceiling is None:
                recommendations.max_bitrate_ceiling = 10000

    if observations.interlace_confidence >= 0.75:
        if config.get("allow_filter_adjustments", True):
            _append_once(recommendations.filters, "bwdif")
            recommendations.reasons.append("interlaced content requires deinterlacing")
        if config.get("allow_force_reencode", True):
            recommendations.force_reencode = True

    if observations.crop_confidence >= 0.8 and observations.crop_filter:
        if config.get("allow_filter_adjustments", True):
            _append_once(recommendations.filters, observations.crop_filter)
            recommendations.reasons.append("detected stable letterboxing warrants crop filtering")
        if config.get("allow_force_reencode", True):
            recommendations.force_reencode = True

    return recommendations
