# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Postrule — graduated-autonomy classification primitive.

Six-phase lifecycle from hand-written rule to learned classifier,
with a statistical transition gate at every phase, a safety-critical
cap that refuses construction in the highest-autonomy phase for
authorization-class decisions, a circuit breaker that reverts to the
rule on ML failure, and shadow-path isolation that keeps observational
classifiers from affecting user-visible output.

Core public API: :class:`LearnedSwitch`, :func:`ml_switch` decorator,
:class:`Phase`, :class:`SwitchConfig`, :class:`ClassificationRecord`,
:class:`FileStorage`, and the language model/ML protocol interfaces.

Tooling (analyzer, ROI reporter, AST-based `wrap_function`, viz,
research runners) ships in submodules — import directly:
``from postrule.analyzer import analyze``, ``from postrule.roi import
compute_switch_roi``, etc.

See README.md and https://postrule.ai.
"""

__version__ = "1.1.0"

from postrule.autoresearch import (
    CandidateHarness,
    CandidateReport,
    Tournament,
    TournamentReport,
)
from postrule.core import (
    BulkVerdict,
    BulkVerdictSummary,
    ClassificationRecord,
    ClassificationResult,
    Label,
    LearnedSwitch,
    Phase,
    SwitchConfig,
    SwitchStatus,
    Verdict,
)
from postrule.decorator import ml_switch
from postrule.gates import (
    AccuracyMarginGate,
    CompositeGate,
    Gate,
    GateDecision,
    ManualGate,
    McNemarGate,
    MinVolumeGate,
    next_phase,
    prev_phase,
)
from postrule.ml import (
    ImagePixelLogRegHead,
    MLHead,
    MLHeadFactory,
    MLPrediction,
    SklearnTextHead,
    TfidfGradientBoostingHead,
    TfidfHeadBase,
    TfidfLinearSVCHead,
    TfidfMultinomialNBHead,
    available_ml_heads,
    make_ml_head,
    register_ml_head,
)
from postrule.ml_strategy import (
    CardinalityMLHeadStrategy,
    FixedMLHeadStrategy,
    MLHeadStrategy,
)
from postrule.models import (
    AnthropicAdapter,
    AnthropicAsyncAdapter,
    LlamafileAdapter,
    LlamafileAsyncAdapter,
    ModelClassifier,
    ModelPrediction,
    OllamaAdapter,
    OllamaAsyncAdapter,
    OpenAIAdapter,
    OpenAIAsyncAdapter,
)
from postrule.storage import (
    BoundedInMemoryStorage,
    FileStorage,
    InMemoryStorage,
    ResilientStorage,
    SqliteStorage,
    Storage,
    StorageBase,
    deserialize_record,
    flock_supported,
    serialize_record,
)
from postrule.switch_class import Switch
from postrule.telemetry import (
    ListEmitter,
    NullEmitter,
    StdoutEmitter,
    TelemetryEmitter,
)
from postrule.verdicts import (
    CallableVerdictSource,
    HumanReviewerSource,
    JudgeCommittee,
    JudgeSource,
    NoVerifierAvailableError,
    VerdictSource,
    WebhookVerdictSource,
    default_verifier,
)

# Default-on hosted-API verdict telemetry. Best-effort, fails silent.
# Auto-installs the cloud emitter as the process-wide default iff the
# user is signed in (``~/.postrule/credentials`` exists) AND has not
# opted out via ``$POSTRULE_NO_TELEMETRY``. The decision happens in
# ``maybe_install`` itself; this block just triggers the check.
#
# The import + call is wrapped so a missing optional dependency, a
# broken credentials file, or a malformed env override never aborts
# ``import postrule``. The decision path is observability-unaware: if
# anything here misfires, the user gets a NullEmitter and life goes
# on. Out of scope for this block (handled by other agents): the
# sign-up flow's consent banner that announces the default-on
# decision to the user.
try:  # pragma: no cover — observability hook; intentionally fails silent
    from postrule.cloud import verdict_telemetry as _verdict_telemetry  # pragma: no cover

    _verdict_telemetry.maybe_install()  # pragma: no cover
except BaseException:  # noqa: BLE001 — observability hook, fails silent  # pragma: no cover
    pass  # pragma: no cover

__all__ = [
    "AccuracyMarginGate",
    "AnthropicAdapter",
    "AnthropicAsyncAdapter",
    "BoundedInMemoryStorage",
    "BulkVerdict",
    "BulkVerdictSummary",
    "CandidateHarness",
    "CandidateReport",
    "CardinalityMLHeadStrategy",
    "CompositeGate",
    "ClassificationRecord",
    "ClassificationResult",
    "FileStorage",
    "FixedMLHeadStrategy",
    "Gate",
    "GateDecision",
    "ImagePixelLogRegHead",
    "InMemoryStorage",
    "Label",
    "LearnedSwitch",
    "ListEmitter",
    "LlamafileAdapter",
    "LlamafileAsyncAdapter",
    "MLHead",
    "MLHeadFactory",
    "MLHeadStrategy",
    "MLPrediction",
    "ManualGate",
    "McNemarGate",
    "MinVolumeGate",
    "ModelClassifier",
    "ModelPrediction",
    "NullEmitter",
    "OllamaAdapter",
    "OllamaAsyncAdapter",
    "OpenAIAdapter",
    "OpenAIAsyncAdapter",
    "Phase",
    "ResilientStorage",
    "SklearnTextHead",
    "SqliteStorage",
    "TfidfGradientBoostingHead",
    "TfidfHeadBase",
    "TfidfLinearSVCHead",
    "TfidfMultinomialNBHead",
    "StdoutEmitter",
    "Storage",
    "StorageBase",
    "Switch",
    "SwitchConfig",
    "SwitchStatus",
    "CallableVerdictSource",
    "HumanReviewerSource",
    "JudgeCommittee",
    "JudgeSource",
    "TelemetryEmitter",
    "Tournament",
    "TournamentReport",
    "Verdict",
    "VerdictSource",
    "NoVerifierAvailableError",
    "WebhookVerdictSource",
    "__version__",
    "available_ml_heads",
    "default_verifier",
    "deserialize_record",
    "flock_supported",
    "make_ml_head",
    "ml_switch",
    "next_phase",
    "prev_phase",
    "register_ml_head",
    "serialize_record",
]
