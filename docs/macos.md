# autonomouse on a MacBook

One command, `bash setup/macos.sh`, creates a Linux virtual machine (VM) on your Mac and runs
autonomouse and a [browsermaze](https://github.com/riodeuno/browsermaze) browser pool in it.
autonomouse needs Linux, so nothing of autonomouse runs on macOS itself. The Mac keeps two
things: the `browsermaze login` command, which logs your sites in with Chrome, and an ssh name
for the VM, `lima-autonomouse`.

This page covers the setup, the VM's daily use (start, stop, shell, ssh), the board, the
browser logins, and removal. After the setup, [setup.md](setup.md) and [running.md](running.md)
apply inside the VM as they do on any Ubuntu machine.

## What you need

- A Mac with macOS 13.5 or later, Apple silicon or Intel. The VM uses the Mac's own processor
  type and Apple's virtualization framework.
- [Homebrew](https://brew.sh). The script installs [Lima](https://lima-vm.io), the VM manager,
  and [uv](https://docs.astral.sh/uv/) with it when they are missing.
- Google Chrome in `/Applications` (or `CHROME` set to a Chrome executable). `browsermaze login`
  opens it on the Mac. The script stops before it creates anything when Chrome is missing:
  `brew install --cask google-chrome` fixes that.
- A checkout of this repository. The script uses the browsermaze checkout next to it
  (`../browsermaze`) and clones it there from GitHub when it is missing.
- The accounts [setup.md](setup.md#what-the-machine-needs) lists: Claude, GitHub and Telegram.
- Memory and disk for the VM: 8 GiB of memory (or half the Mac's, when that is less) and up to
  60 GiB of disk.

## Set up

In a terminal, in your autonomouse checkout:

```
bash setup/macos.sh
```

The first run takes several minutes: it downloads Ubuntu 24.04, then the installer's tools.
Stay at the terminal, because the script asks its questions there. In this order, it:

1. Checks the Mac: macOS, Homebrew, Chrome. Installs Lima and uv with Homebrew when missing.
2. Finds the browsermaze checkout.
3. Creates and starts the VM `autonomouse`.
4. Installs the browser pool in the VM.
5. Adds `Include ~/.lima/*/ssh.config` as the first line of `~/.ssh/config` (after saving a
   copy next to it as `config.backup-<date>-<time>`), then installs the `browsermaze` command on the Mac.
6. Builds autonomouse on the Mac, copies it into the VM and runs `setup/install.sh` there. Its
   questions come last because the final one, `autonomouse project new`, ends in a live
   Claude session. They are the ones [setup.md](setup.md#install) lists: the GitHub token, the
   Telegram bot token, when the first wake runs, and the first project. The installer skips the
   browser pool question, because the pool is the VM's own.
7. Sets `browser.pool` to `<your user>@127.0.0.1` and `browser.slots` to the pool's slot count.
8. Prints a checklist: one `[ok]` or `[FAIL]` line per item, a `fix:` line under each failure.
   The exit status is 0 when everything is in place and 1 when anything failed.

**Claude login.** A new VM has no Claude login, and the installer stops until there is one. The
script then runs `claude auth login` in the VM for you and runs the installer again. Unverified:
how the login completes in a VM with no browser. Expect a link to open on the Mac and a code to
paste back.
A run with no terminal, such as one started from another script, skips the login and prints
the command to run instead: `limactl shell autonomouse claude auth login`.

Running the script again changes nothing that is already in place. It starts a stopped VM, and
the installers skip what they installed and ask nothing they already know.

| Option | Default | Meaning |
|---|---|---|
| `--cpus N` | 4, or the Mac's CPU count when lower | virtual CPUs |
| `--memory GIB` | 8, or half the Mac's memory when lower | memory in GiB |
| `--disk GIB` | 60 | disk size in GiB. The disk file grows only as the VM fills it |
| `--slots N` | the pool's own, 4 | browser slots, 1 to 9 |
| `--browsermaze DIR` | `../browsermaze` | a browsermaze checkout to use |

The size options apply when the VM is created. The defaults are estimates, not measurements.
Each slot is a Chrome with its own display (about 1 GiB). A wake also runs several Claude Code
sessions, so four slots need about 8 GiB, and Lima's own default of 4 GiB would swap. Four CPUs
is Lima's default. The disk holds Ubuntu and the tools (about 10 GiB) and then the browser
profiles, the repository clones and a worktree per card; 60 GiB caps how much of the Mac a
runaway run can fill.

## What it creates

```
Your Mac                                         VM "autonomouse" (Ubuntu 24.04)
  browsermaze login                                 browser pool: Chrome slots, ports 9301 and up,
    Chrome: you log in to your sites  --ssh-------> listening on 127.0.0.1 only
  ssh lima-autonomouse, limactl shell ---------->   autonomouse: board, cron wakes, Claude sessions
                                                      |  ssh <you>@127.0.0.1 browsermaze lease
                                                      +--> the pool, to borrow a logged-in Chrome
```

| Where | What |
|---|---|
| Mac, Homebrew | `lima` and `uv`, when they were missing |
| Mac, `~/.lima/autonomouse` | the VM: its disk, its logs (`ha.stderr.log`) and `ssh.config` |
| Mac, `~/.ssh/config` | one added first line, `Include ~/.lima/*/ssh.config`, and the backup |
| Mac, browsermaze | the `browsermaze` command in `~/.local/bin` (a link into the checkout), `gum` beside it, `~/.config/browsermaze/config`, and the login Chrome's profile in `~/browsers-login` |
| VM, system | the pool: Chrome, Xvfb, x11vnc, ffmpeg and socat; the `browser` account; `/srv/browsers`; the `browsermaze` command in `/usr/local/bin` and its systemd units |
| VM, your user | everything `setup/install.sh` installs ([setup.md](setup.md#install)); an ssh key `~/.ssh/id_ed25519` your user uses to ssh to itself, with the VM's host key in `known_hosts`; copies of the installers in `~/.local/share/autonomouse-macos` |
| VM, autonomouse settings | `browser.pool = <your user>@127.0.0.1` and `browser.slots` |

The VM's settings: Ubuntu 24.04 for the Mac's own processor type, Apple's virtualization
framework (`vmType: vz`), your Mac user name as the VM user, passwordless `sudo` for that
user (Lima's default), no containerd, and **no folder of your Mac mounted**. Lima mounts your
home folder read-only by default. The script turns that off, because agents run in the VM with
the VM user's rights and a mounted home folder would show them the Mac's files. Copy files in
with `scp` or `limactl copy` (below).

## Start and stop the VM

```
limactl start autonomouse
limactl stop autonomouse
limactl list
```

Wakes run from cron inside the VM, so they fire only while the VM runs. A wake that was due
while the VM was stopped does not run later ([running.md](running.md#the-timer)). After a
restart of the Mac, run `limactl start autonomouse`. To start it at every login, run
`limactl autostart enable autonomouse`. Unverified: that a sleeping Mac stops the VM, and so
the wakes.

## Get a shell or ssh into the VM

```
limactl shell autonomouse                  # a shell as your user in the VM
limactl shell autonomouse -- ls ~          # one command
ssh lima-autonomouse                       # the same VM over ssh
```

`ssh lima-autonomouse` works because of the `Include` line in `~/.ssh/config`. Lima writes
`~/.lima/autonomouse/ssh.config` when the VM first starts. The name answers on `127.0.0.1` at a
port Lima chose; `limactl list --format '{{.SSHLocalPort}}' autonomouse` prints it. The key is
Lima's own, `~/.lima/_config/user`. Anything that speaks ssh can use the name, for example
VS Code Remote-SSH, `scp` and `rsync`:

```
scp notes.txt lima-autonomouse:                      # Mac to VM
limactl copy autonomouse:projects/out.txt .          # VM to Mac
```

When `ssh lima-autonomouse` says `Could not resolve hostname`, the `Include` line is missing or
not the first line of `~/.ssh/config`, or the VM has never started.

## Open the board

The board is a terminal screen that runs in the VM ([running.md](running.md#open-the-board)).
From the Mac:

```
ssh -t lima-autonomouse '~/.local/bin/autonomouse'
```

Or run `autonomouse` in a `limactl shell autonomouse` session. ssh does not read the VM's
`~/.profile`, so the first form gives the command's full path. To keep the screen open when the
connection drops, run it inside `tmux` in the VM.

## Log the browsers in

The pool's Chromes start with the logins you give the golden profile. On the Mac:

```
browsermaze login
```

It opens a Chrome on the Mac. Log in to each site the agents need, leave one tab open on each,
and choose **Update the browser pool**. The logins travel over ssh to the VM (`lima-autonomouse`).
Slots leased after the update have them. [browsermaze's README](https://github.com/riodeuno/browsermaze#2-your-logins-mac-or-linux-desktop)
has the details. Two commands show the result:

```
ssh lima-autonomouse browsermaze logins        # the sites the golden profile is logged in to
ssh lima-autonomouse browsermaze status        # one line per slot
```

A site that rejects imported logins needs a login on the pool itself, through VNC
([browsermaze](https://github.com/riodeuno/browsermaze/blob/main/docs/how-it-works.md#logging-in-on-the-pool-through-vnc)):

```
ssh -L 5900:127.0.0.1:5900 lima-autonomouse browsermaze golden start
open vnc://localhost:5900
```

The VNC password is in `/srv/browsers/vnc-password.txt` in the VM, readable by root:
`ssh lima-autonomouse sudo cat /srv/browsers/vnc-password.txt`. When done, run
`ssh lima-autonomouse browsermaze golden stop`.

## Reach the VM from another machine

Unverified: neither route below has been run; the steps come from Lima's documentation and OpenSSH.

Lima forwards the VM's ssh port to `127.0.0.1` on the Mac, so only the Mac can reach the VM.
Both routes need the other machine's public key in the VM's `~/.ssh/authorized_keys`:

```
ssh lima-autonomouse 'cat >> ~/.ssh/authorized_keys' < other-machine.pub
```

| Route | What it needs | How |
|---|---|---|
| Tunnel through the Mac (try this first) | Remote Login turned on in macOS (System Settings, General, Sharing). No `sudo`, no change to the VM | From the other machine, with `PORT` from `limactl list --format '{{.SSHLocalPort}}' autonomouse` on the Mac: `ssh -N -L 2222:127.0.0.1:PORT you@mac` in one terminal, then `ssh -p 2222 <vm user>@127.0.0.1` in another. The port can change after the VM is recreated |
| Bridged networking | `socket_vmnet` built from source and installed as root in `/opt/socket_vmnet`; a sudoers file from `limactl sudoers` installed with `sudo`; the VM stopped and given the network | `limactl stop autonomouse`, `limactl edit autonomouse --network=lima:bridged`, `limactl start autonomouse`. The VM then has an address on the LAN (on `lima0`; the `bridged` network uses `en0`, set in `~/.lima/_config/networks.yaml`), which `limactl shell autonomouse -- ip -4 addr show lima0` prints. Connect with `ssh <vm user>@<that address>` |

[Lima's VMNet page](https://lima-vm.io/docs/config/network/vmnet/) lists the `socket_vmnet`
install steps; it advises against the Homebrew build of `socket_vmnet`. The pool's browser
ports stay private on both routes, because the pool listens on `127.0.0.1` inside the VM. A
bridged VM exposes everything else it listens on to the LAN, its ssh port first.

## Update, resize and remove

**Update.** Pull both checkouts (`autonomouse` and `browsermaze`), then run `bash setup/macos.sh`
again.

**Resize.** The size options apply when the VM is created. For an existing VM, stop it first:
`limactl stop autonomouse`, then `limactl edit autonomouse --memory 12` (`--cpus` and `--disk`
work the same), then `limactl start autonomouse`.

**Remove everything.** Deleting the VM deletes the board, the repository clones, the worktrees,
the golden profile and the Claude login. Push or copy out what you need first.

```
limactl stop autonomouse
limactl delete autonomouse
```

Then, on the Mac, remove what the setup added. Skip any line whose thing you use for other work.

```
rm ~/.local/bin/browsermaze ~/.local/bin/gum
rm -r ~/.config/browsermaze ~/.local/state/browsermaze-setup ~/browsers-login
rm -r ~/.lima                         # also removes every other Lima VM you have
brew uninstall lima uv
```

Edit `~/.ssh/config` to delete its first line, `Include ~/.lima/*/ssh.config`, and delete the
`config.backup-*` files next to it. Delete the `browsermaze` checkout when the script cloned it.

## When a checklist item fails

Each `[FAIL]` line has a `fix:` line. After you fix the cause, run `bash setup/macos.sh` again.
The logs:

| Log | Where |
|---|---|
| Lima and the VM's start | `~/.lima/autonomouse/ha.stderr.log` on the Mac |
| The autonomouse installer | `~/.local/state/autonomouse-install/` in the VM |
| The pool's setup | `~/.local/state/browsermaze-setup/` in the VM, and on the Mac for the `browsermaze` command |

Open questions (2026-10-16): the script and this page have not run on a real Mac yet. The Claude
login in the VM, a sleeping Mac, and the routes to the VM from another machine are unverified.
