# M6 Security And Multi-Node Prep Notes

## Implemented

- Firewall preflight contract with explicit connectivity guard and rollback plan.
- Firewall apply approval task contract using `firewall.apply` placeholder execution.
- Automatic rollback event is armed after approved firewall task execution.
- Local socket prototype contract at `/api/agents/local-socket`.
- Remote agent API contract at `/api/agents/remote-contract`.
- Remote agent register, heartbeat, and heartbeat-loss simulation endpoints.

## Destructive Test Gap

No dedicated destructive Linux firewall test environment is configured for this workspace. M6 therefore does not apply real firewall rules or intentionally break SSH/network connectivity on the development machine.

The current verification covers the non-destructive contract:

- connectivity guard must be confirmed before a firewall approval task can be created;
- rollback plan must be confirmed before a firewall approval task can be created;
- approved firewall task records `automatic_rollback`;
- remote heartbeat loss simulation returns degraded/offline status;
- local socket and remote agent contracts are exposed for future real agent transport work.

Before enabling real firewall mutation, run the same flow in an expendable Linux VM with out-of-band console access and add tests that prove rollback restores connectivity after an intentionally bad rule.
