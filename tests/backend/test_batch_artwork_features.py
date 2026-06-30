from app.domain.schemas import PolygonAsset, PreflightReport, SheetParentSpec
from app.services.batch_artworks import ArtworkClassifier, ArtworkFeatureExtractor
from app.services.geometry import rectangle_asset


def test_feature_extractor_calculates_bbox_area_confidence_and_concavity() -> None:
    polygon = PolygonAsset(
        shape_id="window_box",
        outer=[(0, 0), (120, 0), (120, 80), (0, 80)],
        holes=[[(20, 20), (40, 20), (40, 40), (20, 40)]],
    )
    report = PreflightReport(
        filename="box.svg",
        source_format="svg",
        can_parse_directly=True,
        requires_conversion=False,
        requires_manual_review=False,
    )

    feature = ArtworkFeatureExtractor().extract([polygon], preflight_report=report)

    assert feature.bbox is not None
    assert feature.bbox.width == 120
    assert feature.bbox.height == 80
    assert feature.area == 9200
    assert feature.hole_count == 1
    assert feature.concavity > 0
    assert feature.parse_confidence == 0.95
    assert feature.needs_manual_review is False


def test_classifier_assigns_filler_anchor_full_sheet_and_oversize() -> None:
    extractor = ArtworkFeatureExtractor()
    classifier = ArtworkClassifier()
    parent = SheetParentSpec(width=787, height=1092)

    filler = extractor.extract([rectangle_asset("small", 80, 60)])
    anchor = extractor.extract([rectangle_asset("anchor", 420, 360)])
    full = extractor.extract([rectangle_asset("full", 760, 900)])
    oversize = extractor.extract([rectangle_asset("oversize", 1200, 900)])

    assert classifier.classify(filler, parent=parent, source_format="svg") == "FILLER"
    assert classifier.classify(anchor, parent=parent, source_format="svg") == "ANCHOR"
    assert classifier.classify(full, parent=parent, source_format="svg") == "FULL_SHEET"
    assert classifier.classify(oversize, parent=parent, source_format="pdf") == "OVERSIZE"
