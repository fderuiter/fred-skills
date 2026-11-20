# Contract: advertising zstd input requires enabling the zstd decoder.
def build_pipeline_config(
    advertise_zstd: bool, enable_zstd_decoder: bool
) -> dict[str, bool]:
    if advertise_zstd and not enable_zstd_decoder:
        raise ValueError("advertise_zstd requires enable_zstd_decoder")
    return {
        "advertise_zstd": advertise_zstd,
        "enable_zstd_decoder": enable_zstd_decoder,
    }
