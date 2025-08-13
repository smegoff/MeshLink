## Redundant BBS with Peer Sync

MeshMini can run on multiple radios and **gossip** new posts across peers. If any node goes offline, others continue to serve the board.

**How it works**
1. Every few minutes each BBS advertises latest post IDs (`#SYNC INV`).  
2. Peers request only what they’re missing (`#SYNC GET`).  
3. Sender transfers the post in small parts (`#SYNC POST/PART/END`).  
4. Duplicates are ignored via a unique `uid`.

**Diagrams**
![Peer Architecture](docs/diagrams/peer_architecture_brand.png)
![Sync Flow](docs/diagrams/peer_sequence_brand.png)

**Admin quick-start**
```text
peer add <!peerNodeId>
peer list
sync now   # kick off an inventory immediately
```
