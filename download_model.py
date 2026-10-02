import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))
from index_translate_core.models import download_model

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="独立下载并校验节点模型")
    parser.add_argument("--directory", required=True)
    parser.add_argument("--model", choices=["2B", "9B"], default="2B")
    parser.add_argument("--source", choices=["modelscope", "huggingface"], default="modelscope")
    args = parser.parse_args()
    result = download_model("IndexTeam/Index-Translate-" + args.model, args.directory, args.source)
    print("模型完整：", result["complete"], result["path"])
