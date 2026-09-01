from prompts import validate_prompt_configuration


def test_extraction_v4_prompt_is_available_and_valid():
    validate_prompt_configuration(
        system_version="system_v2",
        extraction_version="extraction_v4",
    )
