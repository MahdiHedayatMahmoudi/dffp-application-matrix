from exporters import _portable_manifest


def test_exported_manifest_removes_windows_local_paths():
    package = (
        r"C:\Users\Example.User\Documents\project"
        r"\scientific_source_packages\paper__abc123"
    )

    manifest = {
        "source_package": {
            "package_dir": package,
            "clean_markdown_path": (
                package + r"\docling\document.clean.md"
            ),
            "source_filename": "paper.pdf",
            "source_sha256": "abc",
        },
        "source_bundle_path": (
            package + r"\dffp\source_bundle.txt"
        ),
        "extraction_run": {
            "response_id": "resp_example",
            "requested_model": "test-model",
        },
        "semantic_repair_runs": [
            {
                "response_id": "resp_example_2",
                "requested_model": "test-model",
            }
        ],
        "final_structured_run": {
            "response_id": "resp_example_3",
            "requested_model": "test-model",
        },
    }

    portable = _portable_manifest(manifest)

    assert (
        portable["source_package"]["package_dir"]
        == "paper__abc123"
    )

    assert (
        portable["source_package"]["clean_markdown_path"]
        == "docling/document.clean.md"
    )

    assert (
        portable["source_bundle_path"]
        == "dffp/source_bundle.txt"
    )

    assert "response_id" not in portable["extraction_run"]

    assert (
        "response_id"
        not in portable["semantic_repair_runs"][0]
    )

    assert (
        "response_id"
        not in portable["final_structured_run"]
    )

    rendered = str(portable)

    assert "Example.User" not in rendered
    assert r"C:\Users" not in rendered

    # Critical: sanitization must not modify the runtime object.
    assert (
        manifest["source_package"]["package_dir"]
        == package
    )

    assert (
        manifest["extraction_run"]["response_id"]
        == "resp_example"
    )

def test_portable_manifest_is_idempotent_for_already_relative_paths():
    manifest = {
        "source_package": {
            "package_dir": "paper__abc123",
            "clean_markdown_path": "docling/document.clean.md",
        },
        "source_bundle_path": "dffp/source_bundle.txt",
        "output_portability": {
            "absolute_local_paths_included": False,
            "path_policy": "source-package-relative-or-basename",
        },
    }

    once = _portable_manifest(manifest)
    twice = _portable_manifest(once)

    assert twice == once
    assert twice["source_package"]["package_dir"] == "paper__abc123"
    assert twice["source_package"]["clean_markdown_path"] == "docling/document.clean.md"
    assert twice["source_bundle_path"] == "dffp/source_bundle.txt"
