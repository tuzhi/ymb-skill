# Batch Account Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve unknown self-account placeholders during stage-two integration when another file in the same batch provides reliable account evidence.

**Architecture:** Add a conservative, source-file-level resolution pass to `integrate.py` after input loading and before cross-file deduplication. Group evidence by normalized self-name and bank, prefer a sole explicit account, require multiple exact transaction overlaps when multiple accounts exist, regenerate transaction IDs after replacement, and report unresolved ambiguity for manual review.

**Tech Stack:** Python 3.11, pandas, unittest, existing `ymb_standardization_core.core` fingerprint helpers.

---

### Task 1: Account resolution behavior

**Files:**
- Create: `bank-statement-standardization/tests/test_integrate_account_resolution.py`
- Modify: `bank-statement-standardization/scripts/integrate.py`

- [x] **Step 1: Write failing tests**

Cover three behaviors with in-memory pandas data:

```python
def test_single_explicit_account_resolves_unknown_source(): ...
def test_multiple_accounts_without_overlap_remain_unresolved(): ...
def test_multiple_accounts_use_two_transaction_overlaps(): ...
```

Assert the resolved account values, resolution method, unresolved review record, and regenerated transaction IDs.

- [x] **Step 2: Verify the tests fail**

Run:

```bash
venv/bin/python -m unittest bank-statement-standardization.tests.test_integrate_account_resolution
```

Expected: failure because `resolve_batch_accounts` does not exist.

- [x] **Step 3: Implement normalization and conservative resolution**

Add helpers in `integrate.py` for normalized identity keys, unknown-account detection, overlap signatures, source-file resolution, and transaction-ID regeneration. Return structured resolution details and unresolved ambiguity details without modifying groups that lack sufficient evidence.

- [x] **Step 4: Verify focused tests pass**

Run the Task 1 unittest command and expect all tests to pass.

### Task 2: Integrate the pass into stage two

**Files:**
- Modify: `bank-statement-standardization/scripts/integrate.py`
- Modify: `bank-statement-standardization/tests/test_integrate_account_resolution.py`

- [x] **Step 1: Write an end-to-end failing test**

Create two temporary standardized CSV files representing one explicit-account XLSX and one unknown-account PDF with duplicate transactions. Call `integrate()` and assert that resolution happens before deduplication, leaving one account and folding duplicate transactions.

- [x] **Step 2: Verify the end-to-end test fails**

Run the focused unittest module and confirm the duplicate rows remain before implementation wiring.

- [x] **Step 3: Wire resolution before deduplication**

Call the resolver immediately after `load_inputs()`, regenerate IDs, then call `dedup_cross_file()`. Add `批次内账号归并` to the report, add ambiguous groups to `人工复核事项`, and describe the new order in `整合策略`.

- [x] **Step 4: Verify focused and integration suites**

Run:

```bash
venv/bin/python -m unittest bank-statement-standardization.tests.test_integrate_account_resolution
venv/bin/python -m unittest discover -s bank-statement-standardization/tests
```

Expected: focused tests pass; any unrelated pre-existing full-suite failure is reported rather than altered.

### Task 3: Real Zebra batch verification

**Files:**
- No production file changes expected.

- [x] **Step 1: Regenerate the Zebra deliverable**

Run `package_deliverable.py` for `bank-statement-standardization/testdata/斑马商业对公流水` with account type `对公` into a new test output directory.

- [x] **Step 2: Inspect the integrated result and report**

Assert that the CMB PDF rows are assigned account `791912215110008`, PDF/XLSX duplicate transactions are folded, and the report records the batch resolution method and source file.

- [x] **Step 3: Run final diff and syntax checks**

Run `git diff --check` and the focused unittest module before reporting completion.
