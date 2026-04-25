#!/usr/bin/env python3
"""Build-time script to download EfficientNet-B0 and export it to OpenVINO IR format.

Usage:
    python scripts/prepare_models.py [--output-dir resources/models]

Requires (install separately, not in requirements.txt):
    pip install torch torchvision
    pip install openvino>=2025.0
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

MODEL_NAME = "scene_classifier"
INPUT_SHAPE = (1, 3, 224, 224)


def _require_torch():
  try:
    import torch
    import torchvision

    return torch, torchvision
  except ImportError:
    print(
      "ERROR: torch and torchvision are required to prepare models.\nInstall them with:  pip install torch torchvision",
      file=sys.stderr,
    )
    sys.exit(1)


def _require_openvino():
  try:
    import openvino as ov

    return ov
  except ImportError:
    print(
      "ERROR: openvino is required to prepare models.\nInstall it with:  pip install openvino>=2025.0",
      file=sys.stderr,
    )
    sys.exit(1)


def _export_onnx(torch, torchvision, onnx_path: str) -> None:
  print("Loading EfficientNet-B0 (pretrained=True) from torchvision…")
  model = torchvision.models.efficientnet_b0(weights=torchvision.models.EfficientNet_B0_Weights.DEFAULT)
  model.eval()

  example_input = torch.zeros(INPUT_SHAPE)
  print(f"Exporting to ONNX: {onnx_path}")
  torch.onnx.export(
    model,
    example_input,
    onnx_path,
    opset_version=13,
    input_names=["input"],
    output_names=["output"],
    dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
  )
  print("ONNX export complete.")


def _convert_to_ir(ov, onnx_path: str, output_dir: str) -> None:
  xml_path = os.path.join(output_dir, f"{MODEL_NAME}.xml")
  print(f"Converting ONNX → OpenVINO IR: {xml_path}")
  ov_model = ov.convert_model(onnx_path, input=[INPUT_SHAPE])
  ov.save_model(ov_model, xml_path, compress_to_fp16=False)
  bin_path = os.path.join(output_dir, f"{MODEL_NAME}.bin")
  print(f"IR saved:\n  {xml_path}\n  {bin_path}")


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument(
    "--output-dir",
    default=str(Path(__file__).parent.parent / "resources" / "models"),
    help="Directory to write scene_classifier.xml and .bin (default: resources/models)",
  )
  args = parser.parse_args()

  output_dir = args.output_dir
  os.makedirs(output_dir, exist_ok=True)

  torch, torchvision = _require_torch()
  ov = _require_openvino()

  with tempfile.TemporaryDirectory() as tmpdir:
    onnx_path = os.path.join(tmpdir, f"{MODEL_NAME}.onnx")
    _export_onnx(torch, torchvision, onnx_path)
    _convert_to_ir(ov, onnx_path, output_dir)

  print("\nModel preparation complete.")


if __name__ == "__main__":
  main()
