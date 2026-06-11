import sys, os
from dataclasses import dataclass
from enum import Enum

@dataclass
class ExamplePath:
    cpp: str
    halide_bin: str
    micro_halide_bin: str
    halide_debug_0_log: str
    halide_debug_1_log: str
    halide_debug_2_log: str
    micro_halide_log: str

    def __init__(self, cpp_path):
        assert cpp_path.endswith(".cpp"), cpp_path
        assert all(c == "_" or c == "." or c == "/" or c.isalnum() for c in cpp_path), cpp_path
        assert "_halide" not in cpp_path, cpp_path
        no_ext = cpp_path[:-4]
        self.cpp = cpp_path
        self.halide_bin = os.path.join("build", no_ext + "_halide")
        self.micro_halide_bin = os.path.join("build", no_ext + "_micro_halide")
        self.halide_debug_0_log = self.halide_bin + ".debug_0.log"
        self.halide_debug_1_log = self.halide_bin + ".debug_1.log"
        self.halide_debug_2_log = self.halide_bin + ".debug_2.log"
        self.micro_halide_log = self.micro_halide_bin + ".log"

example_paths = []

for dirpath, _, filenames in os.walk("examples"):
    for f in filenames:
        if f.endswith(".cpp"):
            fpath = os.path.join(dirpath, f)
            print(f"Found {fpath}")
            example_paths.append(ExamplePath(fpath))

example_paths.sort(key=lambda e: e.cpp)


def gen_ninja():
    out = open("build.ninja", "w")

    out.write("""halide_dir = ../build
cxx = c++
cflags = -std=c++17 -O2

rule compile_halide
  command = $cxx $cflags $in -MD -MF $out.d -I$halide_dir/include -Ihalide_compat -L$halide_dir/src -lHalide -Wl,-rpath,$halide_dir/src -o $out
  depfile = $out.d

rule compile_micro_halide
  command = $cxx $cflags $in -DUSE_MICRO_HALIDE=1 -MD -MF $out.d -Imicro_halide -o $out
  depfile = $out.d

rule run_debug_0
  command = ./$in 2> $out

rule run_debug_1
  command = HL_DEBUG_CODEGEN=1 ./$in 2> $out

rule run_debug_2
  command = HL_DEBUG_CODEGEN=2 ./$in 2> $out

    """)

    for e in example_paths:
        out.write(f"""
build {e.halide_bin}: compile_halide {e.cpp}
build {e.micro_halide_bin}: compile_micro_halide {e.cpp}
build {e.halide_debug_0_log}: run_debug_0 {e.halide_bin}
build {e.halide_debug_1_log}: run_debug_1 {e.halide_bin}
build {e.halide_debug_2_log}: run_debug_2 {e.halide_bin}
build {e.micro_halide_log}: run_debug_0 {e.micro_halide_bin}
""")

    out.close()


def test_all():
    harness_log_fname = "harness_log.txt"
    harness_log = open(harness_log_fname, "w")

    def print_log(*args, **kwargs):
        print(*args, file=sys.stdout, **kwargs)
        print(*args, file=harness_log, **kwargs)

    any_cpp_fail = False
    any_diff_fail = False
    for e in example_paths:
        halide_cpp_fail = 0 != os.system(f"ninja {e.halide_bin}")
        micro_halide_cpp_fail = 0 != os.system(f"ninja {e.micro_halide_bin}")
        any_cpp_fail |= halide_cpp_fail | micro_halide_cpp_fail
        if halide_cpp_fail:
            print_log(f"Failed to compile C++: `ninja {e.halide_bin}` failed")
        if micro_halide_cpp_fail:
            print_log(f"Failed to compile C++: `ninja {e.micro_halide_bin}` failed")
        if not halide_cpp_fail and not micro_halide_cpp_fail:
            os.system(f"ninja {e.halide_debug_2_log}")
            os.system(f"ninja {e.halide_debug_1_log}")
            halide_runtime_fail = 0 != os.system(f"ninja {e.halide_debug_0_log}")
            micro_halide_runtime_fail = 0 != os.system(f"ninja {e.micro_halide_log}")

            if halide_runtime_fail and not micro_halide_runtime_fail:
                any_diff_fail = True
                print_log(f"Behavior difference: {e.halide_bin} failed, but {e.micro_halide_bin} exited successfully")
            if not halide_runtime_fail and micro_halide_runtime_fail:
                any_diff_fail = True
                print_log(f"Behavior difference: {e.halide_bin} exited successfully, but {e.micro_halide_bin} failed")
            if halide_runtime_fail and micro_halide_runtime_fail:
                print_log(f"Negative example PASS: {e.cpp}")
            if not halide_runtime_fail and not micro_halide_runtime_fail:
                diff_cmd = f"python3 canonicalize.py --diff {e.halide_debug_0_log} {e.micro_halide_log}"
                diff_code = os.system(diff_cmd) >> 8
                if diff_code == 0:
                    print_log(f"Positive example PASS: {e.cpp}")
                elif diff_code == 1:
                    any_diff_fail = True
                    print_log(f"Non-trivial loop nest difference (failed command: {diff_cmd!r})")
                else:
                    any_diff_fail = True
                    print_log(f"Canonicalizer failed; check if micro_halide output {e.micro_halide_log} seems syntactically correct, and flag for human review if no issues spotted (failed command: {diff_cmd!r})")

    if any_cpp_fail:
        print_log("Not all C++ files compiled successfully. DO NOT spawn micro-agents.")
    elif any_diff_fail:
        print_log("Not all runtime tests passed.")
    else:
        print_log("All tests passed.")

    print_log(f"Logged to {harness_log_fname}")
    harness_log.close()
    sys.exit(any_cpp_fail | any_diff_fail)


callback_dict = {
    "gen_ninja": gen_ninja,
    "test_all": test_all,
}

if len(sys.argv) != 2:
    raise ValueError("Expect exactly one command line argument")

arg = sys.argv[1]

if arg not in callback_dict:
    raise ValueError(f"Unexpected command line argument {arg!r}, not in {sorted(callback_dict.keys())}")

callback_dict[arg]()
