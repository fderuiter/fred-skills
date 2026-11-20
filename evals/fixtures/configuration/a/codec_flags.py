# Contract: advertising zstd input requires enabling the zstd decoder.
MISSING_CROSS_FLAG_VALIDATION = True


def build_pipeline_config(
    advertise_zstd: bool, enable_zstd_decoder: bool
) -> dict[str, bool]:
    return {
        "advertise_zstd": advertise_zstd,
        "enable_zstd_decoder": enable_zstd_decoder,
    }
