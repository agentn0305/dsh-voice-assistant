window.__ModuleLoader__.load({
	id: "dsh-voice-stop-button",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
		const react = require("react");
		const jsxRuntime = require("react/jsx-runtime");

		/** 本机 TTS 服务的停止端点，对应 src/tts_server.py 的 do_POST /stop。 */
		const STOP_URL = "http://127.0.0.1:8765/stop";
		/** 「已停止 / 未在朗读」提示的驻留时间（毫秒）。 */
		const FLASH_MS = 1500;

		const buttonStyle = {
			display: "inline-flex",
			alignItems: "center",
			gap: 5,
			height: 24,
			padding: "0 6px",
			border: "none",
			borderRadius: 4,
			background: "transparent",
			font: "inherit",
			fontSize: 12,
			lineHeight: "24px",
			cursor: "pointer",
			opacity: 0.75
		};
		const iconStyle = {
			display: "inline-block",
			flex: "0 0 auto",
			width: 8,
			height: 8,
			borderRadius: 1,
			background: "currentColor"
		};

		/**
		 * 挂在 conversation.chat.assistant-actions 槽位里，也就是每条已完成的助手消息底部的操作条。
		 * 点击只发一次 POST /stop：成功显示「已停止」，失败显示「未在朗读」，不抛异常也不弹窗。
		 */
		function VoiceStopButton() {
			const [phase, setPhase] = react.useState("idle");
			const timer = react.useRef(null);

			react.useEffect(() => () => {
				if (timer.current !== null) clearTimeout(timer.current);
			}, []);

			const flash = (next) => {
				if (timer.current !== null) clearTimeout(timer.current);
				setPhase(next);
				timer.current = setTimeout(() => {
					timer.current = null;
					setPhase("idle");
				}, FLASH_MS);
			};

			const stop = react.useCallback(() => {
				fetch(STOP_URL, { method: "POST" })
					.then((res) => flash(res.ok ? "stopped" : "offline"))
					.catch(() => flash("offline"));
			}, []);

			const label = phase === "stopped" ? "已停止" : phase === "offline" ? "未在朗读" : "停止朗读";

			return jsxRuntime.jsxs("button", {
				type: "button",
				title: "停止朗读",
				"aria-label": label,
				"data-voice-stop": "",
				onClick: stop,
				style: {
					...buttonStyle,
					color: phase === "stopped"
						? "var(--dsw-alias-state-success-primary, #2f9e68)"
						: "var(--dsw-alias-label-caption, inherit)"
				},
				children: [
					jsxRuntime.jsx("span", { key: "stop-icon", "aria-hidden": "true", style: iconStyle }),
					jsxRuntime.jsx("span", { key: "stop-label", style: { whiteSpace: "nowrap" }, children: label })
				]
			});
		}

		/** 无宿主侧依赖，也没有要注入的 Client 服务。 */
		function apply(ctx) {
			ctx.slots.inject("conversation.chat.assistant-actions", () => ctx.slots.register({
				name: "conversation.chat.assistant-actions",
				id: "voice-stop",
				order: 30
			}, VoiceStopButton));
		}

		/** 客户端插件必须声明依赖的服务；不写 "slots"，ctx.slots 就是 undefined，激活会直接失败。 */
		const inject = ["slots"];

		exports.apply = apply;
		exports.inject = inject;
		return module.exports;
	}
});
