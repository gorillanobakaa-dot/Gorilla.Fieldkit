"""Live run 16, stop 8 (2026-10-02 06:49): GNU patch with --fuzz=3 placed the owner's early-return block of
toolkit/components/glean/xpcom/FOG.cpp seven lines above its context, before the function signature; the compile
stopped with "expected unqualified-id". The verifier had called the hunk APPLIED: the lines existed. Now an added
block must sit next to its own context, and the group apply uses no fuzz at all."""
from fieldkit.buildh import firefox, verify

H = {"lines": [
    "                    const bool aDisableInternalPings) {",
    "   MOZ_ASSERT(XRE_IsParentProcess());",
    "   gInitializeCalled = true;",
    "+",
    "+  // Gorilla: skip Glean initialization entirely - the glean.dispatche thread",
    "+  // wastes ~3.5% parent CPU on metric bookkeeping we don't need. Upload is",
    "+  // already disabled; this prevents the dispatcher thread from being spawned.",
    "+  return NS_OK;",
    "+",
    "   RunOnShutdown(",
    "       [&] {"]}

GOOD = """NS_IMETHODIMP
FOG::InitializeFOG(const nsACString& aDataPathOverride,
                   const bool aDisableInternalPings) {
  MOZ_ASSERT(XRE_IsParentProcess());
  gInitializeCalled = true;

  // Gorilla: skip Glean initialization entirely - the glean.dispatche thread
  // wastes ~3.5% parent CPU on metric bookkeeping we don't need. Upload is
  // already disabled; this prevents the dispatcher thread from being spawned.
  return NS_OK;

  RunOnShutdown(
      [&] {
""".splitlines()

BAD = """NS_IMETHODIMP

  // Gorilla: skip Glean initialization entirely - the glean.dispatche thread
  // wastes ~3.5% parent CPU on metric bookkeeping we don't need. Upload is
  // already disabled; this prevents the dispatcher thread from being spawned.
  return NS_OK;

FOG::InitializeFOG(const nsACString& aDataPathOverride,
                   const bool aDisableInternalPings) {
  MOZ_ASSERT(XRE_IsParentProcess());
  gInitializeCalled = true;
  RunOnShutdown(
      [&] {
""".splitlines()


def test_a_block_next_to_its_context_is_placed_and_one_above_the_signature_is_not():
    assert firefox.misplaced(GOOD, H) is None
    why = firefox.misplaced(BAD, H)
    assert why and "gInitializeCalled" in why
    assert verify.score_hunk(GOOD, H, "FOG.cpp")[0] == "APPLIED"
    v, d = verify.score_hunk(BAD, H, "FOG.cpp")
    assert v == "NOT-APPLIED" and d.startswith("misplaced")


def test_the_group_apply_uses_no_fuzz():
    import inspect
    src = inspect.getsource(firefox.step_apply_group)
    assert '"--fuzz=0"' in src and '"--fuzz=3"' not in src


def test_tier_zero_does_not_call_a_misplaced_block_already_in_place():
    assert firefox.already_upstream(GOOD, H)
    assert not firefox.already_upstream(BAD, H)
