# Existing English Triage model review

Reviewed and evaluated on 2026-09-19. The existing `CNN_Triage` model is retained as an optional English category baseline, not used to control SupportOps routing, priorities, permissions, or actions. Its fresh category macro-F1 on the 50 English SupportOps test tickets was **0.3235**. No old project metric is reused.

## Reviewed artifacts

The read-only source project was `E:\Projects\Intelligent Root Cause & Urgency Triage`. Review covered `backend/main.py`, the code cells of `Intelligent_Root_Cause_&_Urgency_Triage.ipynb`, model configuration/metadata, and the two class CSV files. Its application code was not imported or executed. SupportOps loaded only the saved weights and embedded vocabulary using an explicitly supplied implementation of the reviewed `custom_standardize` function. The source project and weights were not modified or copied into SupportOps.

| Artifact | SHA-256 |
|---|---|
| `incident_triage_artifacts/best_triage_model.keras` | `a36b168bdcbdfc81b1bd8f2824d82a4ec57fd5e5daeb87687df814b79d633798` |
| `incident_triage_artifacts/root_cause_classes.csv` | `beca6c59518a181dfdec54d2706f0c6b4254287b389254de4f6ad416f15146a8` |
| `incident_triage_artifacts/urgency_classes.csv` | `649854f87971fbfb38128972b74c91452519a1323acef247a72f58aaccb19f06` |
| `backend/main.py` | `53225738562211ccb97f3a523b0794f7e25e9d3a4677475f1525566ba3abcc70` |
| Notebook | `97f0ed2629ab03538d9c2faffe42645c44b056b2cb9c2ec9368bb94a92f74fe4` |

## Model and data limitations

- The notebook trains on English rows from `Tobi-Bueck/customer-support-tickets`. It combines subject and body, maps ticket `type` to `root_cause_family`, and maps `priority` to `urgency`. The first head predicts **Incident / Request / Problem / Change**, not a verified technical root cause. The second predicts high / low / medium.
- Preprocessing lowercases, removes URLs, strips characters outside `[a-z0-9\s]`, and normalizes whitespace. It removes Cyrillic and alters Turkish letters; RU/TR scores would not be a meaningful supported-language comparison.
- The saved model embeds `TextVectorization` with a 30,000-token cap and 220-token sequence length, a 128-dimensional embedding, Conv1D, max pooling, dense/dropout layers, and two softmax heads. These are not multilingual retrieval embeddings. Long tickets are truncated and unknown terms receive the vocabulary's unknown-token handling.
- The notebook creates stratified row splits with seed 42 and adapts the vocabulary on training text only. It does not demonstrate grouping of duplicates or paraphrases across those splits. It selects the winning architecture using its old test scores, so that old test set also served model selection. Those old metrics are unsuitable as an untouched generalization estimate and are not reproduced here.
- The old application's confidence scores are softmax values; no calibration, abstention threshold, or uncertainty validation was found in the reviewed code. Its textual explanations and operational recommendations are separate rules, not causal evidence from the neural network.
- The SupportOps corpus describes a fictional product and uses complete ticket text without a separate subject. This introduces both domain and input-format differences from the source training setup. There are no independently labelled urgency targets here, so urgency, SLA quality, retrieval, factual support, and next-step selection were not scored for Triage.

## Fresh held-out comparison

The original `best_triage_model.keras` was fixed before this run. No retraining, threshold selection, translation, prompt tuning, or test-label-based repair was performed. Both systems below received the same 50 English test texts from frozen `synthetic-v1`.

| Category classifier | n | Macro-F1 | Correct / n | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|
| Existing CNN_Triage | 50 | 0.3235 | 20 / 50 | 27.4568 | 42.9106 |
| SupportOps deterministic demo-only proxy | 50 | 0.9732 | 49 / 50 | 0.0600 | 0.1455 |

The demo classifier uses manually authored RetailBridge rules. Its larger score on this small, related synthetic corpus does not establish superiority over learned models on real tickets. This comparison is not an LLM benchmark. Triage timings cover one-ticket eager CPU inference, include the first inference, and exclude model loading (991.38 ms), imports, and HTTP. Demo timings cover the analysis function. Different work is timed; these numbers are local observations, not a deployment performance comparison.

| True category | Predicted Incident | Predicted Request | Predicted Problem | Predicted Change | F1 |
|---|---:|---:|---:|---:|---:|
| Incident | 13 | 4 | 9 | 4 | 0.5532 |
| Request | 1 | 2 | 2 | 4 | 0.2500 |
| Problem | 2 | 0 | 4 | 0 | 0.3478 |
| Change | 1 | 1 | 2 | 1 | 0.1429 |

All 50 predictions, raw class scores, timings, expected labels, and model hash are in [triage_test.json](../data/evaluation/triage_test.json). The 30 errors are retained in [triage_test_failures.json](../data/evaluation/triage_test_failures.json). The corresponding SupportOps results are in [EVALUATION.md](EVALUATION.md) and [test_demo.json](../data/evaluation/test_demo.json).

## Reproduce

The actual run used the already installed system Python 3.13.15, TensorFlow 2.21.0, Keras 3.15.0, NumPy 2.2.1, h5py 3.14.0, ml_dtypes 0.5.4, and protobuf 6.32.1 on Windows 11 Pro 10.0.26200, AMD Ryzen 7 6800H CPU. No dependency was installed or changed for this run; SupportOps `.venv` remains separate. The saved artifact metadata records Keras 3.15.0. TensorFlow reported CPU execution and warned that Conv1D discards the upstream embedding mask. The model was evaluated unchanged. Native Windows GPU limitations are described in the [TensorFlow installation guide](https://www.tensorflow.org/install/pip).

```powershell
# Run from SupportOps with a TensorFlow-capable Python environment.
python .\scripts\evaluate.py --split test --triage-only --triage-root 'E:\Projects\Intelligent Root Cause & Urgency Triage'
```

To prepare a separate environment on another machine, use an ignored `.triage-venv` and install compatible TensorFlow/Keras there; neither dependency is needed for the default SupportOps demo. The class CSV and model must remain together in `incident_triage_artifacts` under the supplied root.

The run validates the unchanged dataset manifest before inference. The test file SHA-256 is `01da5457aff0b6198c226226a8b063fd803a5435e5459bc2dabaab6f268fd60b`. These are 50 hand-authored scenario groups, not a random customer sample. Any future model adaptation should use development data and a fresh held-out set for new quality claims. External model calls and paid services were not used.
