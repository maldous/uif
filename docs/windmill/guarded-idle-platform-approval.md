# Binding a fixed Windmill action to guarded Stokowski idle

This module is a non-deployed verifier, not a substitute start command or an
approval-record writer. The existing signed installer, apply, and activation
entrypoints remain unchanged. There is no generic Bash, CLI or public handler
for this adapter. Missing private composition fails closed.

The trusted deployment binds the exact Windmill workspace `foo`, dedicated
runnable path, immutable script hash (canonical lowercase 16/64-hex in the
local adapter), initiating actor and effective `permissioned_as` separately.
These are protected configuration, not fields a caller can choose. A script
launch from an approved service identity does not by itself prove the initiating
actor. The private dispatch binds the validated server-issued run UUID to its
actual executing non-preview job and the exact original operation, independently
accepted review/bundle and qualified preflight for one installer/apply/start
phase. A phase cannot substitute for another. The lookup takes no caller data.
Shared runner args, shell environment echo, clipboard JSON and an OAuth token
sent to Foo are not proof.

Only an isolated protected Stokowski controller may call `qualify` under the
existing selector lock. The controller re-observes real quiescence and exact
selection as required by its phase, checks the result lifetime at the mutation
edge, persists the original intent under the existing lock, and verifies the
same-operation effect receipt. It must never remove the activation condition or
marker consumption fence, issue legacy signed files, replay ambiguous effects
or start a normal inference profile. The proof returned here is not accepted
by the current signed controller; deployment must bind the authoritative job
lookup and explicitly compose a reviewed private effect entrypoint. No
duplicated human signature is needed on that future authenticated path, but
a green pure verifier does not establish a deployed entrypoint.
