import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

function quotePowerShell(value) {
  return `"${String(value).replaceAll('"', '\\"')}"`;
}

function buildAdminCommands({ name, ip, mask, gateway, dns }) {
  const address = gateway
    ? `netsh interface ipv4 set address name=${quotePowerShell(name)} static ${ip} ${mask} ${gateway}`
    : `netsh interface ipv4 set address name=${quotePowerShell(name)} static ${ip} ${mask}`;
  const dnsCommand = dns
    ? `netsh interface ipv4 set dnsservers name=${quotePowerShell(name)} static ${dns} primary`
    : "";
  return [address, dnsCommand].filter(Boolean).join("\n");
}

async function runPowerShell(command) {
  const { stdout } = await execFileAsync(
    "powershell.exe",
    ["-NoProfile", "-Command", `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); ${command}`],
    { windowsHide: true }
  );
  return stdout;
}

function assertSafeText(value, field) {
  const text = String(value || "").trim();
  if (!text) {
    throw new Error(`${field} is required.`);
  }
  if (/[&|;<>^"`$]/.test(text)) {
    throw new Error(`${field} contains unsupported characters.`);
  }
  return text;
}

function assertIp(value, field, allowEmpty = false) {
  const text = String(value || "").trim();
  if (!text && allowEmpty) return "";
  const parts = text.split(".");
  if (parts.length !== 4 || parts.some((part) => !/^\d{1,3}$/.test(part) || Number(part) > 255)) {
    throw new Error(`${field} must be an IPv4 address.`);
  }
  return text;
}

function normalizeEthernetConfig(config) {
  const name = assertSafeText(config.name, "Adapter name");
  const ip = assertIp(config.ip, "IP");
  const mask = assertIp(config.mask, "Subnet mask");
  const gateway = assertIp(config.gateway, "Gateway", true);
  const dns = assertIp(config.dns, "DNS", true);
  const adminCommand = buildAdminCommands({ name, ip, mask, gateway, dns });
  return { name, ip, mask, gateway, dns, adminCommand };
}

export async function listEthernetAdapters() {
  const command = [
    "Get-NetAdapter |",
    "Where-Object { $_.Status -ne 'Disabled' } |",
    "Select-Object Name,InterfaceDescription,Status,LinkSpeed |",
    "ConvertTo-Json -Depth 3"
  ].join(" ");
  const stdout = await runPowerShell(command);
  const parsed = stdout.trim() ? JSON.parse(stdout) : [];
  return Array.isArray(parsed) ? parsed : [parsed];
}

export async function getEthernetInfo(name) {
  const adapterName = assertSafeText(name, "Adapter name");
  const command = [
    `$name = ${quotePowerShell(adapterName)};`,
    "$adapter = Get-NetAdapter -Name $name -ErrorAction Stop;",
    "$ip = Get-NetIPConfiguration -InterfaceAlias $name;",
    "[pscustomobject]@{",
    "Name=$adapter.Name;",
    "InterfaceDescription=$adapter.InterfaceDescription;",
    "Status=$adapter.Status;",
    "LinkSpeed=$adapter.LinkSpeed;",
    "IPv4Address=($ip.IPv4Address.IPAddress -join ', ');",
    "IPv4PrefixLength=($ip.IPv4Address.PrefixLength -join ', ');",
    "Gateway=($ip.IPv4DefaultGateway.NextHop -join ', ');",
    "DnsServer=($ip.DNSServer.ServerAddresses -join ', ')",
    "} | ConvertTo-Json -Depth 3"
  ].join(" ");
  const stdout = await runPowerShell(command);
  return JSON.parse(stdout);
}

export function buildEthernetCommand(config) {
  const { name, ip, mask, gateway, dns, adminCommand } = normalizeEthernetConfig(config);
  return {
    ok: true,
    adapter: name,
    currentRequested: { ip, mask, gateway, dns },
    adminCommand
  };
}

export async function openEthernetCommandTerminal(config) {
  const plan = buildEthernetCommand(config);
  const command = plan.adminCommand.replaceAll("\n", " & ");
  const script = [
    "$cmd = " + quotePowerShell(command) + ";",
    "Start-Process -FilePath 'cmd.exe' -Verb RunAs -ArgumentList '/k', $cmd"
  ].join(" ");
  await execFileAsync("powershell.exe", ["-NoProfile", "-Command", script], {
    windowsHide: true
  });
  return {
    ok: true,
    message: "已请求打开管理员 CMD 终端。请在 Windows UAC 弹窗中确认后执行命令。",
    adminCommand: plan.adminCommand
  };
}

export async function getEthernetPrivilegeStatus() {
  try {
    await execFileAsync("net.exe", ["session"], { windowsHide: true });
    return {
      ok: true,
      isAdmin: true,
      message: "当前服务具备管理员权限，可以从网页修改以太网配置。"
    };
  } catch {
    return {
      ok: true,
      isAdmin: false,
      message: "当前服务没有管理员权限。网页可以发送配置，但 Windows 不允许它修改系统网卡 IP。"
    };
  }
}

export async function applyEthernetConfig(config) {
  const { name, ip, mask, gateway, dns, adminCommand } = normalizeEthernetConfig(config);
  const addressArgs = gateway
    ? ["interface", "ipv4", "set", "address", `name="${name}"`, "static", ip, mask, gateway]
    : ["interface", "ipv4", "set", "address", `name="${name}"`, "static", ip, mask];

  try {
    const address = await execFileAsync("netsh.exe", addressArgs, { windowsHide: true });

    let dnsResult = null;
    if (dns) {
      dnsResult = await execFileAsync(
        "netsh.exe",
        ["interface", "ipv4", "set", "dnsservers", `name="${name}"`, "static", dns, "primary"],
        { windowsHide: true }
      );
    }

    return {
      ok: true,
      adapter: name,
      ip,
      mask,
      gateway,
      dns,
      adminCommand,
      stdout: [address.stdout, dnsResult?.stdout].filter(Boolean).join("\n").trim()
    };
  } catch (error) {
    const detail = [error.stdout, error.stderr, error.message].filter(Boolean).join("\n").trim();
    const wrapped = new Error(
      "修改以太网失败。通常是当前服务没有管理员权限，或网卡名称/状态不正确。请用管理员身份运行 VS Code/终端后重试。"
    );
    wrapped.statusCode = 500;
    wrapped.details = {
      adapter: name,
      ip,
      mask,
      gateway,
      dns,
      adminCommand,
      commandOutput: detail
    };
    throw wrapped;
  }
}
