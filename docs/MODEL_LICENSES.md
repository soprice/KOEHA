# Model licenses

KOEHA's source code uses the [MIT License](../LICENSE). The installer and portable
ZIP omit model weights. KOEHA downloads them when requested, and the model
publishers' terms apply to those downloads.

| Model | License shown by the publisher | Source for the pinned revision |
| --- | --- | --- |
| Qwen3-ASR 1.7B GGUF | Apache-2.0 | [GGUF conversion](https://huggingface.co/ggml-org/Qwen3-ASR-1.7B-GGUF/tree/36a678687ba7d07a74ca70ccb0e36902e005fb80) · [Qwen source model](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) |
| Parakeet TDT-CTC 0.6B Japanese | CC BY 4.0 | [NVIDIA model card](https://huggingface.co/nvidia/parakeet-tdt_ctc-0.6b-ja/blob/44edb27eea9317daf89333e75eb830db4b1cc298/README.md) · [CC BY 4.0 terms](https://creativecommons.org/licenses/by/4.0/) |
| Fun-ASR-Nano-2512 | Apache-2.0 | [FunAudioLLM model card](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512-hf/blob/7ef6cafdc445b5beff0b730cbeb51952c793ca6e/README.md) |
| Nemotron 3.5 ASR Streaming 0.6B | OpenMDW-1.1 | [NVIDIA model card](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/blob/ea30d66debe3740a08b573244286791d423d6b3e/README.md) · [OpenMDW-1.1 terms](https://openmdw.ai/license/1-1/) |
| Whisper Large v3 | Apache-2.0 | [OpenAI model card](https://huggingface.co/openai/whisper-large-v3/blob/06f233fe06e710322aca913c1bc4249a0d71fce1/README.md) |
| Whisper Large v3 Turbo | MIT | [OpenAI model card](https://huggingface.co/openai/whisper-large-v3-turbo/blob/41f01f3fe87f28c78e2fbf8b568835947dd65ed9/README.md) |

The Qwen conversion repository links the original model card. Both downloaded
GGUF files match the SHA-256 values pinned by KOEHA and contain
`general.license = apache-2.0`, matching the original publisher's license.
Check the applicable license and notices before
redistributing model files. OpenMDW-1.1 calls for keeping the agreement and
applicable notices with redistributed material. CC BY 4.0 requires attribution
when sharing material under that license.

KOEHA also downloads the [Silero VAD model](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/LICENSE)
and the [llama.cpp Vulkan runtime](https://github.com/ggml-org/llama.cpp/blob/b12110/LICENSE)
separately. Both upstream sources use MIT. This page covers the model catalog,
VAD, and llama.cpp. Other dependencies retain their own terms.
