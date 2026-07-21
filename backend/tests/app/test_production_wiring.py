from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.runtime import _build_production_pipelines


class ProductionRuntimeWiringTests(unittest.TestCase):
    @patch("app.runtime.generation_service_from_config")
    @patch("app.runtime.AdvancedRetriever.from_artifacts")
    @patch("app.runtime.BGEEncoder")
    def test_explicit_paths_are_injected_into_the_retriever(
        self,
        encoder_type: Mock,
        retriever_factory: Mock,
        generation_factory: Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.yaml"
            config.write_text(
                "paths:\n  artifacts: custom-artifacts\n"
                "retrieval:\n  embed_model: custom-bge\n  use_reranker: false\n",
                encoding="utf-8",
            )
            encoder = Mock()
            encoder_type.return_value = encoder
            retriever = Mock()
            retriever_factory.return_value = retriever
            generation = Mock()
            generation_factory.return_value = generation

            actual_retriever, actual_generation, _ = _build_production_pipelines(
                "custom-chunks.jsonl",
                sources_path="custom-sources.csv",
                metadata_path="custom.sqlite3",
                config_path=config,
            )

        self.assertIs(actual_retriever, retriever)
        self.assertIs(actual_generation, generation)
        args = retriever_factory.call_args.args
        kwargs = retriever_factory.call_args.kwargs
        self.assertEqual(args[:2], ("custom-chunks.jsonl", "custom-artifacts"))
        self.assertIs(args[2], encoder)
        self.assertEqual(kwargs["sources_path"], "custom-sources.csv")
        self.assertEqual(kwargs["metadata_path"], "custom.sqlite3")
        self.assertFalse(kwargs["use_reranker"])


if __name__ == "__main__":
    unittest.main()
