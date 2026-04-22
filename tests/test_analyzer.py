"""Tests for analyzer recommendations."""

from resources.analyzer import AnalyzerObservations, build_recommendations


class TestBuildRecommendations:
    def test_disabled_analyzer_returns_no_recommendations(self):
        recommendations = build_recommendations(AnalyzerObservations(content_type="animation"), {"enabled": False})

        assert recommendations.codec_order == []
        assert recommendations.bitrate_ratio_multiplier is None
        assert recommendations.max_bitrate_ceiling is None
        assert recommendations.preset is None
        assert recommendations.filters == []
        assert recommendations.force_reencode is False
        assert recommendations.reasons == []

    def test_animation_reorders_codecs_and_reduces_bitrate(self):
        recommendations = build_recommendations(AnalyzerObservations(content_type="animation"))

        assert recommendations.codec_order == ["av1", "h265", "h264"]
        assert recommendations.bitrate_ratio_multiplier == 0.9
        assert recommendations.max_bitrate_ceiling == 8000
        assert any("animation" in reason for reason in recommendations.reasons)

    def test_talking_head_prefers_slow_preset_and_lower_bitrate(self):
        recommendations = build_recommendations(AnalyzerObservations(content_type="talking_head"))

        assert recommendations.preset == "slow"
        assert recommendations.bitrate_ratio_multiplier == 0.85
        assert recommendations.max_bitrate_ceiling == 4000

    def test_high_motion_prefers_more_bitrate_and_faster_preset(self):
        recommendations = build_recommendations(AnalyzerObservations(content_type="sports_high_motion"))

        assert recommendations.preset == "fast"
        assert recommendations.bitrate_ratio_multiplier == 1.15
        assert recommendations.max_bitrate_ceiling == 12000

    def test_noise_adds_denoise_filter_and_retains_higher_multiplier(self):
        recommendations = build_recommendations(AnalyzerObservations(content_type="sports_high_motion", noise_score=0.9))

        assert "hqdn3d" in recommendations.filters
        assert recommendations.bitrate_ratio_multiplier == 1.15
        assert recommendations.max_bitrate_ceiling == 12000

    def test_interlace_adds_deinterlace_and_force_reencode(self):
        recommendations = build_recommendations(AnalyzerObservations(interlace_confidence=0.9))

        assert recommendations.filters == ["bwdif"]
        assert recommendations.force_reencode is True

    def test_crop_adds_crop_filter_and_force_reencode(self):
        recommendations = build_recommendations(AnalyzerObservations(crop_confidence=0.9, crop_filter="crop=1920:800:0:140"))

        assert recommendations.filters == ["crop=1920:800:0:140"]
        assert recommendations.force_reencode is True

    def test_toggle_disables_specific_recommendation_families(self):
        recommendations = build_recommendations(
            AnalyzerObservations(content_type="animation", interlace_confidence=0.9),
            {
                "allow_codec_reorder": False,
                "allow_bitrate_adjustments": False,
                "allow_filter_adjustments": False,
                "allow_force_reencode": False,
            },
        )

        assert recommendations.codec_order == []
        assert recommendations.bitrate_ratio_multiplier is None
        assert recommendations.max_bitrate_ceiling is None
        assert recommendations.filters == []
        assert recommendations.force_reencode is False
