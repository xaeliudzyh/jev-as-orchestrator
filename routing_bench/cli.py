"""Common transport/model options; task definitions live in exp1 and exp2."""
from .experiment import common_parser


def hf_parser(description, experiment, **common_options):
    parser = common_parser(description, experiment, **common_options)
    parser.add_argument("--mode", choices=("local", "api"), default="local")
    parser.add_argument("--adapter", choices=("auto", "frida", "decider", "kev", "jeva", "clef"), default="auto")
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda, cuda:0, mps")
    parser.add_argument("--dtype", choices=("auto", "float32", "float16", "bfloat16"), default="auto")
    parser.add_argument("--revision", help="HF revision/commit to download for local inference")
    parser.add_argument("--cache-dir", help="Local Hugging Face checkpoint cache")
    parser.add_argument("--max-input-tokens", type=int, default=32768)
    parser.add_argument("--jeva-cache", choices=("none", "set", "hybrid"), default="none",
                        help="Jeva option-memory reuse; disabled by default for uncached timings")
    parser.add_argument("--jeva-option-chunk-size", type=int, default=1,
                        help="Maximum Jeva options encoded per batch (1 conserves VRAM)")
    parser.add_argument("--jeva-option-batch-tokens", type=int, default=4096,
                        help="Jeva padded-token batch budget; a single longer option is still encoded whole")
    parser.add_argument("--endpoint", help="Full URL of a deployed native decision endpoint")
    parser.add_argument("--api-format", choices=("hf-handler", "systemone", "cloudflare"), default="hf-handler",
                        help="HF custom-handler envelope, native SystemOne, or Cloudflare Clef REST envelope")
    parser.add_argument("--api-key-env", default="HF_TOKEN")
    parser.add_argument("--clef-quantization", choices=("none", "4bit", "8bit"), default="none",
                        help="Local Clef backbone only; requires bitsandbytes for 4/8-bit loading")
    return parser


def openrouter_parser(description, experiment):
    parser = common_parser(description, experiment)
    parser.add_argument("--endpoint", default="https://openrouter.ai/api/alpha/decisions")
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    return parser
