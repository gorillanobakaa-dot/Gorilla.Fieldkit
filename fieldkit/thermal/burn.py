"""One busy core, for the thermal live-sensor proof (sensors.prove_live). A module, not `python -c`: Python compiles
it once into the shared bytecode cache instead of on every start (rules: compile once, fieldkit compileonce check)."""
if __name__ == "__main__":
    while True:
        pass
