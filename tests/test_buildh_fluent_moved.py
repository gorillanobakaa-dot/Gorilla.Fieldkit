"""Live run 16 (2026-10-01): two Fluent hunks the tier refused for the wrong reasons.

contextual-identity.ftl h2: the owner MOVES `user-context-add-container` down the file. The group apply (GNU patch)
had already added the new copy, so the file held the message twice; this hunk removes the old copy, and the check,
judging by id, said "should be gone" while one copy was meant to stay. Removals are judged by COUNT.

preferences.ftl h52: the owner's tree renames `containers-remove-button3` back to `containers-remove-button2`
("Remove"); an earlier run had already transferred the wording, so the file held the owner's name with the owner's
parts and the old name was gone. The tier paired the two names (drift twin), looked for the old one and deferred
"no longer exists" for a removal it did not need to make. Already transferred is done.
"""
from fieldkit.buildh import fluent

TWICE = """user-context-new-tab =
    .label = New Tab
    .accesskey = N
user-context-add-container =
    .label = Add new container
    .accesskey = A
user-context-manage-containers =
    .label = Manage containers
    .accesskey = o

user-context-icon-fence =
    .label = Fence

user-context-add-container =
    .label = Add new container
    .accesskey = A
""".splitlines()

H2 = {"lines": [
    " user-context-new-tab =",
    "-    .label = New Tab",
    "+    .label = New Gorilla Tab",
    "     .accesskey = N",
    "-user-context-add-container =",
    "-    .label = Add new container",
    "-    .accesskey = A",
    " user-context-manage-containers =",
    "     .label = Manage containers",
    "     .accesskey = o"]}


def test_a_moved_message_loses_one_copy_and_the_check_counts():
    new, notes, gone = fluent.port(TWICE, H2)
    assert gone == [] and "1 removed" in notes[-1]
    assert sum(1 for l in new if l == "user-context-add-container =") == 1
    assert "    .label = New Gorilla Tab" in new
    assert fluent.check(TWICE, new, H2, {}) == []
    # not removing the copy is still caught
    untouched = [l.replace("New Tab", "New Gorilla Tab") for l in TWICE]
    assert fluent.check(TWICE, untouched, H2, {}) == ["message user-context-add-container should be gone (one of its 2 copies)"]


DONE = """containers-new-tab-check3 =
    .label = Select a container for each new Gorilla tab
    .accesskey = S

containers-settings-button2 =
    .title = Gorilla Settings
containers-remove-button2 =
    .title = Remove

account-sync-section =
    .heading = Account and sync
""".splitlines()

H52 = {"lines": [
    " containers-new-tab-check3 =",
    "-    .label = Select a container for each new tab",
    "+    .label = Select a container for each new Gorilla tab",
    "     .accesskey = S",
    " ",
    " containers-settings-button2 =",
    "-    .title = Settings",
    "-containers-remove-button3 =",
    "-    .title = Delete",
    "+    .title = Gorilla Settings",
    "+containers-remove-button2 =",
    "+    .title = Remove",
    " ",
    " ## Account and sync",
    " account-sync-section ="]}


def test_an_already_transferred_twin_is_done_not_gone():
    new, notes, gone = fluent.port(DONE, H52)
    assert gone == []
    assert any("already transferred: containers-remove-button2" in n for n in notes)
    assert new == DONE
    assert fluent.check(DONE, new, H52, {}) == []
    # with the old name still there, the wording still goes onto it (unchanged behaviour)
    old = [l.replace("containers-remove-button2 =", "containers-remove-button3 =").replace(".title = Remove", ".title = Delete") for l in DONE]
    new, notes, gone = fluent.port(old, H52)
    assert gone == [] and "    .title = Remove" in new and "containers-remove-button3 =" in new
