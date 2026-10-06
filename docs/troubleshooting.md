# Things that look broken but are not

**`Dropping notification … after 3 failed signal attempts`** in agent logs. If
the bridge was initialised without `--notification-url` it keeps its default of
`http://localhost/notification`, which nothing serves, so every notification is
retried three times and dropped. Portal software that implements no
notification receiver (Waldur, today) loses nothing by this — and `stack.sh`,
which has no portal software at all, logs it constantly.

**`<portal>.<agent> get_projects <portal>` errors.** That address has to be a
registered offering, not an agent name — see
["register offering"](console.md#register-offering).

**`401 Unauthorized … Date is outside acceptable time window`.** Not the
invite. Every request is signed with a `Date`, and the bridge rejects one more
than **five seconds** from its own clock — so this is clock skew between
wherever signalbox runs and wherever the bridge runs, and under Docker Desktop
it also shows up on its own after the VM's clock jumps. Retrying works; a
container restart fixes it for good. Worth knowing because a 401 otherwise
reads as a bad key.

**A `401` that is not about time.** The client and the bridge must be the same
release: from 0.91.0 the client signs the V2 canonical string and an older
bridge verifies the V1 one. signalbox warns when the versions differ — see
[Native deployment](deployments.md#native-deployment).

**An instruction that comes back "did not complete".** Nothing rejected it — an
instruction aimed at an agent with no route is never refused, it simply never
lands, and the wait expires. Check the destination against the agent's `route`
in the inspector; a peer allocator's agents have none
([why](test-stack.md#two-allocators-on-one-estate)).
