"""Stop 7 of live run 16 as a rule: a header from an excised component, included by code 157 added."""
from fieldkit.buildh import creepfix

LOG = [" 69:06.70 E D:\\build\\firefox\\157.0-truth\\ipc\\glue\\UtilityProcessImpl.cpp(14,12): fatal error: 'mozilla/llama/LlamaRuntimeLinker.h' file not found",
       " 69:06.70 E    14 | #  include \"mozilla/llama/LlamaRuntimeLinker.h\""]

SRC = """#if defined(XP_WIN) && defined(MOZ_SANDBOX)
#  include "mozilla/CpuInfo.h"
#  include "mozilla/llama/LlamaRuntimeLinker.h"
#  include "mozilla/sandboxTarget.h"
#endif

  if (*sandboxingKind == SandboxingKind::HW_INFERENCE) {
    mozilla::llama::LlamaRuntimeLinker::Init();
  }

  mozilla::SandboxTarget::Instance()->StartSandbox();
""".splitlines()


def test_the_stop_is_recognised_and_the_header_traced_to_the_excised_component():
    assert creepfix.missing_headers(LOG) == [("D:/build/firefox/157.0-truth/ipc/glue/UtilityProcessImpl.cpp", "mozilla/llama/LlamaRuntimeLinker.h")]
    assert creepfix.rel_to_tree("D:/build/firefox/157.0-truth/ipc/glue/UtilityProcessImpl.cpp", "D:\\build\\firefox\\157.0-truth") == "ipc/glue/UtilityProcessImpl.cpp"
    assert creepfix.excised_header("mozilla/llama/LlamaRuntimeLinker.h", {"toolkit/components/ml/backends/llama"})
    assert not creepfix.excised_header("mozilla/sandboxTarget.h", {"toolkit/components/ml/backends/llama"})


def test_include_and_self_contained_use_are_excised_with_gorilla_comments():
    new, removed, leftover = creepfix.excise(SRC, "mozilla/llama/LlamaRuntimeLinker.h")
    assert leftover == [] and creepfix.balanced(new)
    assert not any("LlamaRuntimeLinker" in l and "GORILLA" not in l for l in new)
    assert sum(1 for l in new if "GORILLA excised" in l) == 2
    assert "  mozilla::SandboxTarget::Instance()->StartSandbox();" in new and '#  include "mozilla/CpuInfo.h"' in new
    assert [r[1] for r in removed] == ['#  include "mozilla/llama/LlamaRuntimeLinker.h"', "mozilla::llama::LlamaRuntimeLinker::Init();"]


def test_a_use_inside_an_expression_is_left_to_a_person():
    src = SRC[:6] + ["  auto* lib = mozilla::llama::LlamaRuntimeLinker::Get();", "  Use(lib);"] + SRC[9:]
    new, removed, leftover = creepfix.excise(src, "mozilla/llama/LlamaRuntimeLinker.h")
    assert leftover == [7]
