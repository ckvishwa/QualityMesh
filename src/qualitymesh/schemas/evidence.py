"""Strict schema for a release-evidence envelope.

An evidence submission is an external trust boundary, same as the release contract
(see `qualitymesh.schemas.contract`). The envelope wraps raw evidence bytes with the
minimum metadata needed to identify and route them: the declared evidence kind, the
subject it is claimed to be bound to, and a digest QualityMesh itself computes from the
raw bytes — never one supplied by the caller, since a submitter-claimed digest is a
claim, not proof (see `docs/ARCHITECTURE.md` section 11).

The envelope does not carry producer, workflow, run or commit claims. `SYSTEM_RULES.md`
section 9 and the planned ingestion lifecycle in `ARCHITECTURE.md` section 7 reference
such claims, but neither document defines a field set, required/optional status, or
format for them (a commit SHA pattern and a run ID pattern are not specified anywhere in
the repository). Adding unconstrained fields now would not encode a real invariant.
That decision belongs to the evidence-ingestion task (FR-3), which is out of scope here.

Schema validation proves only structural and semantic validity. It does not prove
authenticity, integrity, provenance, contract satisfaction, artifact safety, or release
approval.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, computed_field

from qualitymesh.schemas.contract import EvidenceKind, EvidenceSubject


class StrictEvidenceModel(BaseModel):
    """Base configuration for all evidence-boundary models.

    Mirrors `qualitymesh.schemas.contract.StrictContractModel` exactly. Defined locally
    rather than imported: the two schema families sit at different trust boundaries
    (release policy vs. evidence submission), and sharing a base would couple them for
    no present benefit.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class EvidenceEnvelope(StrictEvidenceModel):
    """Raw evidence bytes plus their declared kind, subject and computed identity.

    Identity is the digest, not a separate field: two envelopes with identical raw
    bytes are the same evidence, which is what makes reprocessing idempotent
    (`SYSTEM_RULES.md` section 9).
    """

    kind: EvidenceKind
    subject: EvidenceSubject
    raw_bytes: bytes

    @computed_field
    @property
    def digest_algorithm(self) -> Literal["sha256"]:
        return "sha256"

    @computed_field
    @property
    def digest(self) -> str:
        return hashlib.sha256(self.raw_bytes).hexdigest()
