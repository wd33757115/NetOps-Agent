# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# version1.0-添加工单连续地址合并功能，华为和华三F50X0版本生成模板已验证
import logging
import os
from hashlib import sha256

from policy_sets import address_text, addresses, cidrs
from policy_window import h3c_range, huawei_range

logger = logging.getLogger(__name__)


def ticket_window(rules: list[dict]):
    """The one window of a ticket on this device; its time range is named after the ticket."""
    windows = {rule["window"] for rule in rules if rule.get("window")}
    if len(windows) > 1:
        raise ValueError("同一工单只能有一个有效期")
    return next(iter(windows), None)


class HuaweiConfigGenerator:
    @staticmethod
    def _format_address(ip_str: str, is_source: bool = True) -> list[str]:
        """格式化华为 USG6000F 系列的源/目的地址"""
        prefix = " source-address" if is_source else " destination-address"
        if "-" in ip_str and ip_str.count(".") == 6:
            start_ip, end_ip = ip_str.split("-")
            return [f"{prefix} range {start_ip} {end_ip}"]
        elif "-" in ip_str and not ip_str.startswith("range"):
            base_ip = ip_str.rsplit(".", 1)[0]
            range_part = ip_str.split(".")[-1]
            start_end = range_part.split("-")
            if len(start_end) != 2:
                logger.error(f"IP范围格式错误: {ip_str}，预期为 'x.x.x.start-end'")
                return [f"{prefix} {base_ip}.{start_end[0]} 32"]
            start = f"{base_ip}.{start_end[0]}"
            end = f"{base_ip}.{start_end[1]}"
            return [f"{prefix} range {start} {end}"]
        elif "/" in ip_str:
            ip, mask = ip_str.split("/")
            return [f"{prefix} {ip} {mask}"]
        return [f"{prefix} {ip_str} 32"]

    @staticmethod
    def _format_ports(ports: set[str], proto: str) -> list[str]:
        """格式化华为 USG6000F 系列的服务端口，支持范围和单端口混合，处理 tcp/udp"""
        if not ports:
            return [f"service {proto.lower()}" if proto else "service any"]

        # 将端口按数值排序并分组处理
        port_list = sorted(ports, key=lambda x: int(x.split("-")[0]) if "-" in x else int(x))
        formatted = []
        i = 0
        while i < len(port_list):
            if "-" in port_list[i]:
                # 直接添加范围端口
                start, end = port_list[i].split("-")
                if proto.lower() == "tcp/udp":
                    formatted.append(f" service protocol tcp destination-port {start} to {end}")
                    formatted.append(f" service protocol udp destination-port {start} to {end}")
                else:
                    formatted.append(
                        f" service protocol {proto.lower()} destination-port {start} to {end}"
                    )
                i += 1
            else:
                # 检查连续端口
                start = int(port_list[i])
                j = i + 1
                while (
                    j < len(port_list)
                    and "-" not in port_list[j]
                    and int(port_list[j]) == start + (j - i)
                ):
                    j += 1
                if j - i > 2:  # 如果连续端口超过2个，使用 range
                    end = int(port_list[j - 1])
                    if proto.lower() == "tcp/udp":
                        formatted.append(f" service protocol tcp destination-port {start} to {end}")
                        formatted.append(f" service protocol udp destination-port {start} to {end}")
                    else:
                        formatted.append(
                            f" service protocol {proto.lower()} destination-port {start} to {end}"
                        )
                    i = j
                else:  # 单端口或少量非连续端口，单独列出
                    if proto.lower() == "tcp/udp":
                        formatted.append(f" service protocol tcp destination-port {start}")
                        formatted.append(f" service protocol udp destination-port {start}")
                    else:
                        formatted.append(
                            f" service protocol {proto.lower()} destination-port {start}"
                        )
                    i += 1

        return formatted

    @staticmethod
    def _generate_rule(rule_data: dict, rule_index: int) -> list[str]:
        """生成华为 USG6000F 系列防火墙的安全策略规则"""
        src_zone, dst_zone = rule_data["rule_key"]
        ticket_id = rule_data["ticket_id"]
        config = [
            f"rule name {ticket_id}_{rule_index}",
            f" description {ticket_id}",
            f" source-zone {src_zone}",
            f" destination-zone {dst_zone}",
        ]

        # 配置源地址
        for src in rule_data.get("sources", []):
            config.extend(HuaweiConfigGenerator._format_address(src, is_source=True))

        # 配置目的地址
        for dst in rule_data.get("destinations", []):
            config.extend(HuaweiConfigGenerator._format_address(dst, is_source=False))

        # 配置服务和端口
        proto = rule_data.get("proto", "").lower()
        ports = rule_data.get("ports", set())
        config.extend(HuaweiConfigGenerator._format_ports(ports, proto))

        # 有效期与长连接（长连接只对 TCP 生效）
        if rule_data.get("window"):
            config.append(f" time-range {ticket_id}")
        if rule_data.get("long_link") is not None:
            config.append(" long-link enable")
            config.append(f" long-link aging-time {rule_data['long_link']}")

        # 配置动作
        action = "permit" if rule_data.get("action", "").lower() == "permit" else "deny"
        config.append(f" action {action}\n")

        return config

    @staticmethod
    def generate(output_dir: str, fw_name: str, rules: list[dict]):
        """生成华为 USG6000F 系列防火墙的配置"""
        config = ["system-view"]
        if window := ticket_window(rules):
            config += [f"time-range {rules[0]['ticket_id']}", f" {huawei_range(window)}", "quit"]
        config.append("security-policy")
        rule_index = 1
        for rule_data in rules:
            config.extend(HuaweiConfigGenerator._generate_rule(rule_data, rule_index))
            config.append("")  # 规则间换行
            rule_index += 1
        config.append("quit\nquit\nquit\nsave\ny\ny\n")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{fw_name}.txt")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join([line for line in config if line.strip()]))
        logger.info(f"生成华为防火墙配置: {output_path}")


class H3CConfigGenerator:
    @staticmethod
    def _format_address(ip_str: str, is_source: bool = True) -> list[str]:
        """格式化 H3C F50X0 系列的源/目的地址"""
        prefix = "source" if is_source else "destination"
        if not ip_str or ip_str.strip() == "":
            logger.warning(f"无效的IP地址: {ip_str}，跳过处理")
            return [f"{prefix}-ip-host 0.0.0.0"]

        # 处理完整范围格式，如 "10.16.152.91-10.16.152.92"
        if "-" in ip_str and ip_str.count(".") == 6:
            start_ip, end_ip = ip_str.split("-")
            return [f"{prefix}-ip-range {start_ip} {end_ip}"]

        # 处理简写范围格式，如 "10.253.22.24-28"
        elif "-" in ip_str and not ip_str.startswith("range"):
            base_ip = ip_str.rsplit(".", 1)[0]
            range_part = ip_str.split(".")[-1]
            start_end = range_part.split("-")
            if len(start_end) != 2:
                logger.error(f"IP范围格式错误: {ip_str}，预期为 'x.x.x.start-end'")
                return [f"{prefix}-ip-host {base_ip}.{start_end[0]}"]
            start = f"{base_ip}.{start_end[0]}"
            end = f"{base_ip}.{start_end[1]}"
            return [f"{prefix}-ip-range {start} {end}"]

        elif "/" in ip_str:
            ip, mask = ip_str.split("/")
            return [f"{prefix}-ip-subnet {ip} {mask}"]

        return [f"{prefix}-ip-host {ip_str}"]

    @staticmethod
    def _format_ports(ports: set, proto: str) -> list[str]:
        """格式化 H3C F50X0 系列的服务端口"""
        if not ports:
            return []
        formatted = []
        for p in sorted(ports):
            if "-" in p:
                start, end = p.split("-")
                formatted.append(f"service-port {proto} destination range {start} {end}")
            else:
                formatted.append(f"service-port {proto} destination eq {p}")
        return formatted

    @staticmethod
    def _generate_rule(rule_data: dict, rule_index: int) -> list[str]:
        """生成 H3C F50X0 系列防火墙的安全策略规则"""
        src_zone, dst_zone = rule_data["rule_key"]
        ticket_id = rule_data["ticket_id"]
        config = [
            f"rule name {ticket_id}-{rule_index}",
            "counting enable",
            f"description {ticket_id}",
            f"source-zone {src_zone}",
            f"destination-zone {dst_zone}",
        ]

        # 配置源地址
        for src in rule_data.get("sources", []):
            config.extend(
                [f"{line}" for line in H3CConfigGenerator._format_address(src, is_source=True)]
            )

        # 配置目的地址
        for dst in rule_data.get("destinations", []):
            config.extend(
                [f"{line}" for line in H3CConfigGenerator._format_address(dst, is_source=False)]
            )

        # 配置服务和端口
        proto = rule_data["proto"] if rule_data["proto"] else "ip"
        if ports := rule_data.get("ports"):
            config.extend(H3CConfigGenerator._format_ports(ports, proto))
        else:
            config.append(f"service {proto}")

        # 有效期与长连接（长连接只对 TCP ESTABLISHED 会话生效）
        if rule_data.get("window"):
            config.append(f"time-range {ticket_id}")
        if rule_data.get("long_link") is not None:
            config.append(f"session persistent aging-time {rule_data['long_link']}")

        # 配置动作
        action = "pass" if rule_data.get("action", "").lower() == "permit" else "deny"
        config.append(f"action {action}\n")

        return config

    @staticmethod
    def generate(output_dir: str, fw_name: str, rules: list[dict]):
        """生成 H3C F50X0 系列防火墙的配置"""
        config = ["system-view"]
        if window := ticket_window(rules):
            config.append(h3c_range(rules[0]["ticket_id"], window))
        config.append("security-policy ip")
        rule_index = 1
        for rule_data in rules:
            config.extend(H3CConfigGenerator._generate_rule(rule_data, rule_index))
            config.append("")
            rule_index += 1
        config.append("return")
        config.append("save force")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{fw_name}.txt")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join([line for line in config if line.strip()]))
        logger.info(f"生成H3C防火墙配置: {output_path}")


class H3CF1000ConfigGenerator:
    @staticmethod
    def _format_address(
        ip_str: str, group_name: str, index: int, is_source: bool = True
    ) -> list[str]:
        """格式化 H3C SecPath F1000 系列的地址对象"""
        prefix = "source" if is_source else "destination"
        config = []

        if not ip_str or ip_str.strip() == "":
            logger.warning(f"无效的IP地址: {ip_str}，跳过处理")
            return [f"{index} network host 0.0.0.0"]

        # 处理完整范围格式，如 "10.16.152.91-10.16.152.92"
        if "-" in ip_str and ip_str.count(".") == 6:
            start_ip, end_ip = ip_str.split("-")
            return [f"{index} network range {start_ip} {end_ip}"]

        # 处理简写范围格式，如 "10.253.22.24-28"
        elif "-" in ip_str and not ip_str.startswith("range"):
            base_ip = ip_str.rsplit(".", 1)[0]
            range_part = ip_str.split(".")[-1]
            start, end = map(int, range_part.split("-"))
            start_ip = f"{base_ip}.{start}"
            end_ip = f"{base_ip}.{end}"
            return [f"{index} network range {start_ip} {end_ip}"]

        # 处理子网格式，如 "10.246.240.0/20"
        elif "/" in ip_str:
            ip, mask = ip_str.split("/")
            return [f"{index} network subnet {ip} {mask}"]

        # 单 IP
        return [f"{index} network host {ip_str}"]

    @staticmethod
    def _format_service(ports: set, proto: str, service_name: str, start_index: int) -> list[str]:
        """格式化 H3C SecPath F1000 系列的服务对象"""
        config = []
        index = start_index

        if not ports:
            config.append(f"{index} service {proto.lower()}")
            return config

        for p in sorted(ports, key=lambda x: int(x.split("-")[0]) if "-" in x else int(x)):
            if "-" in p:
                start, end = p.split("-")
                config.append(f"{index} service {proto.lower()} destination range {start} {end}")
            else:
                config.append(f"{index} service {proto.lower()} destination eq {p}")
            index += 5  # 索引递增 5，符合 H3C 习惯
        return config

    @staticmethod
    def _generate_rule(rule_data: dict, rule_index: int, ticket_id: str) -> list[str]:
        """生成 H3C SecPath F1000 系列防火墙的安全策略规则"""
        src_zone, dst_zone = rule_data["rule_key"]
        proto = rule_data["proto"].lower() if rule_data["proto"] else "ip"
        config = []

        # 生成源地址组
        src_group_name = f"Src_{ticket_id}_{rule_index}"
        src_index = 0
        config.append(f"object-group ip address {src_group_name}")
        for src in rule_data.get("sources", []):
            config.extend(
                H3CF1000ConfigGenerator._format_address(
                    src, src_group_name, src_index, is_source=True
                )
            )
            src_index += 5  # 索引递增 5

        config.append("quit")

        # 生成目的地址组
        dst_group_name = f"Dst_{ticket_id}_{rule_index}"
        dst_index = 0
        config.append(f"object-group ip address {dst_group_name}")
        for dst in rule_data.get("destinations", []):
            config.extend(
                H3CF1000ConfigGenerator._format_address(
                    dst, dst_group_name, dst_index, is_source=False
                )
            )
            dst_index += 5

        config.append("quit")

        # 生成服务组
        service_name = f"{proto}_{ticket_id}_{rule_index}"
        config.append(f"object-group service {service_name}")
        if ports := rule_data.get("ports"):
            config.extend(H3CF1000ConfigGenerator._format_service(ports, proto, service_name, 0))
        else:
            config.append(f"0 service {proto}")

        config.append("quit")

        # 生成安全策略
        config.extend(
            [
                "security-policy ip",
                f" rule name {ticket_id}_{rule_index}",
                f"  description {ticket_id}",
                f"  source-zone {src_zone}",
                f"  destination-zone {dst_zone}",
                f"  source-ip {src_group_name}",
                f"  destination-ip {dst_group_name}",
                f"  service {service_name}",
                f"  action {'pass' if rule_data.get('action', '').lower() == 'permit' else 'drop'}",
                "  quit\n",
            ]
        )
        return config

    @staticmethod
    def generate(output_dir: str, fw_name: str, rules: list[dict]):
        """生成 H3C SecPath F1000 系列防火墙的配置"""
        config = ["system-view"]
        rule_index = 1
        for rule_data in rules:
            ticket_id = rule_data["ticket_id"]
            config.extend(H3CF1000ConfigGenerator._generate_rule(rule_data, rule_index, ticket_id))
            config.append("")
            rule_index += 1
        config.append("return")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{fw_name}.txt")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join([line for line in config if line.strip()]))
        logger.info(f"生成H3C SecPath F1000防火墙配置: {output_path}")


class TopSecConfigGenerator:
    @staticmethod
    def _format_address(ip_str: str) -> list[str]:
        # Exact CIDR decomposition supports cross-subnet ranges without expanding hosts.
        return [f"source-address {net.network_address} {net.netmask}" for net in cidrs(ip_str)]

    @staticmethod
    def _format_ports(ports: set) -> str:
        """格式化天融信防火墙的服务端口"""
        if not ports:
            return ""
        formatted = []
        for p in sorted(ports, key=lambda x: int(x.split("-")[0]) if "-" in x else int(x)):
            if "-" in p:
                start, end = p.split("-")
                formatted.append(f"{start}-{end}")
            else:
                formatted.append(p)
        return " ".join(formatted)

    @staticmethod
    def _generate_rule(rule_data: dict, rule_index: int) -> list[str]:
        """生成天融信防火墙的安全策略规则"""
        src_zone, dst_zone = rule_data["rule_key"]
        ticket_id = rule_data["ticket_id"]
        config = [
            "security-policy",
            f" rule {rule_index} name {ticket_id}-{rule_index}",
            f"  description {ticket_id}",
            f"  source-zone {src_zone}",
            f"  destination-zone {dst_zone}",
        ]
        for src in rule_data.get("sources", []):
            config.extend([f"  {line}" for line in TopSecConfigGenerator._format_address(src)])
        for dst in rule_data.get("destinations", []):
            config.extend(
                [
                    f"  {line.replace('source', 'destination')}"
                    for line in TopSecConfigGenerator._format_address(dst)
                ]
            )
        if ports := rule_data.get("ports"):
            ports_str = TopSecConfigGenerator._format_ports(ports)
            proto = rule_data["proto"] if rule_data["proto"] else "ip"
            config.append(f"  service {proto} port {ports_str}")
        else:
            proto = rule_data["proto"] if rule_data["proto"] else "ip"
            config.append(f"  service {proto}")
        action = "permit" if rule_data.get("action", "").lower() == "permit" else "deny"
        config.append(f"  action {action}")
        return config

    @staticmethod
    def generate(output_dir: str, fw_name: str, rules: list[dict]):
        """生成天融信防火墙的配置"""
        config = ["configure"]
        rule_index = 1
        for rule_data in rules:
            config.extend(TopSecConfigGenerator._generate_rule(rule_data, rule_index))
            config.append("")
            rule_index += 1
        config.append("exit")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{fw_name}.cfg")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join([line for line in config if line.strip()]))
        logger.info(f"生成天融信防火墙配置: {output_path}")


class HillstoneConfigGenerator:
    """StoneOS 5.5 policy/service syntax; see vendor references in README.md."""

    @staticmethod
    def _format_address(ip_str: str, is_source=True) -> list[str]:
        prefix = "src" if is_source else "dst"
        result = []
        for value in address_text(addresses(ip_str)):
            if "-" in value:
                first, last = value.split("-")
                result.append(f"{prefix}-range {first} {last}")
            else:
                result.append(f"{prefix}-ip {value}/32")
        return result

    @staticmethod
    def _generate_rule(rule_data: dict, rule_index: int) -> list[str]:
        proto = rule_data["proto"]
        service_names = []
        commands = []
        # One interval per service object stays within the documented 8-entry limit.
        for port in rule_data["ports"]:
            signature = f"{rule_data['ticket_id']}:{proto}:{port}"
            name = "NP_" + sha256(signature.encode()).hexdigest()[:24]
            service_names.append(name)
            commands += [f"service {name}", f" {proto} dst-port {port.replace('-', ' ')}", "exit"]
        if proto == "icmp":
            service_names = ["ICMP"]
        commands += [
            "policy-global",
            "rule",
            f" name {rule_data['ticket_id']}_{rule_index}",
            f" src-zone {rule_data['rule_key'][0]}",
            f" dst-zone {rule_data['rule_key'][1]}",
        ]
        for source in rule_data["sources"]:
            commands.extend(HillstoneConfigGenerator._format_address(source, True))
        for destination in rule_data["destinations"]:
            commands.extend(HillstoneConfigGenerator._format_address(destination, False))
        commands.extend(f" service {name}" for name in service_names)
        commands += [" action permit", "exit", "exit"]
        return commands

    @staticmethod
    def generate(output_dir: str, fw_name: str, rules: list[dict]):
        config = ["configure"]
        for index, rule in enumerate(rules, 1):
            config.extend(HillstoneConfigGenerator._generate_rule(rule, index))
        config.append("exit")
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, f"{fw_name}.cfg"), "w", encoding="utf-8") as stream:
            stream.write("\n".join(config))
