from __future__ import annotations


MODEL_FEATURE_SUFFIXES = ("think", "search")
MODEL_VARIANT_SUFFIXES = (
    ("think",),
    ("search",),
    ("think", "search"),
)


def split_model_features(model: str) -> tuple[str, set[str]]:
    parts = (model or "").strip().split("-")
    features: set[str] = set()

    while parts and parts[-1].lower() in MODEL_FEATURE_SUFFIXES:
        features.add(parts.pop().lower())

    if not features:
        return (model or "").strip(), features
    return "-".join(parts), features


def expand_model_variants(models: list[str] | tuple[str, ...], excluded_models: set[str] | None = None) -> list[str]:
    excluded = {model.lower() for model in (excluded_models or set())}
    expanded: list[str] = []

    for model in models:
        base_model, features = split_model_features(model)
        expanded.append(model)
        if features or model.lower() in excluded:
            continue
        for suffix in MODEL_VARIANT_SUFFIXES:
            expanded.append(f"{base_model}-{'-'.join(suffix)}")

    return expanded


def model_requests_thinking(model: str) -> bool:
    _, features = split_model_features(model)
    return "think" in features


def model_requests_search(model: str) -> bool:
    _, features = split_model_features(model)
    return "search" in features
