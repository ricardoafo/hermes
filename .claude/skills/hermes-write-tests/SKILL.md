---
name: hermes-write-tests
description: Use whenever you write or change a test in the hermes repo, to follow the unit-test standard (rule first, hand-computed expected values, a red run, properties for maths, state machines for state, mutation check).
---

# Write tests that would catch the bug

AI-written tests tend to assert what the code does, not what it should do.
These steps stop that. The full standard is in
`docs/guides/ai-authored-tests.md`; this is the order to work in.

## Before writing

1. **Name the rule.** Each test protects one rule: a pre-registration line, an
   ADR section, an issue, or a catalogue entry. The test's docstring names it.
   The test name says what can never happen
   (`test_fifo_never_draws_a_lot_opened_after_the_sale`).
2. **Compute the expected value by hand** from the rule, with a worked example
   in a comment if it is not obvious. Never run the code to find out what it
   returns and paste that in.

## Writing

1. **Pin exact values.** Never rely only on `is not None`, truthiness, `> 0`,
   a length or `isinstance`. The `no-vague-test-assertions` guard rejects them.
2. **Every limit gets two tests**: exactly at it and just past it.
3. **One behaviour claim per test.** Assert every claim the name makes, and
   nothing else.
4. **Mock only at the edge**, and name the integration or scenario test that
   covers the seam. Never mock the exchange in domain tests; use the
   in-memory fake. Adapter tests use recorded fixtures (`respx`, `syrupy`).
5. **Maths gets a property test** with Hypothesis (indicators, sizing, tax,
   statistics, parsers). Keep `max_examples` within the CI profile budget.
6. **Anything with state gets a state machine** (`RuleBasedStateMachine`) with
   a small reference model beside the real code and invariants after every
   step. See `tests/unit/**/test_*_state_machine.py`.
7. **Cross-module behaviour belongs in a scenario**, not a unit test full of
   mocks. Use the `hermes-scenario` skill.
8. **SEV-1 paths** carry `# SEV-1: <rubric row>` above the test function.
9. **Credential-shaped literals**, even fake ones, carry `# gitleaks:allow`.

## Prove it

1. **Red first.** Run the new test on the code before your change; it must
    fail for the right reason. For a fix, the test is the reproduction.
2. **Break it on purpose.** Plant the bug the test claims to catch (flip a
    `>` to `>=`, drop a call) and check the test fails. For changes in
    `src/hermes/domain/` or `src/hermes/application/`, run
    `bash scripts/dev/mutation-diff.sh` and report the kill rate.
3. **Ask the one question**: if the invariant broke, would this test fail?
    If not, rewrite or delete it.

## When a code change breaks an existing test: STOP

If the test should have failed on the behaviour change, silencing it defeats
the point. Do not edit an existing assertion to make it pass without a
stated reason in the PR body, and ask first when the behaviour change was
not what the task asked for.

## In the PR body

- `Test tiers touched: unit, property, ...` (required by `create-pr.sh`).
- Whether tests were written before or after the code.
- Mutants killed, new failure modes found, and every `noqa` or
  `type: ignore` added, with its reason.
