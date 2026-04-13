// Polymarket 自动交易 - 前端公共脚本

// 闪烁消息自动消失
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.flash').forEach(function(el) {
        setTimeout(function() {
            el.style.opacity = '0';
            el.style.transition = 'opacity 0.5s';
            setTimeout(function() { el.remove(); }, 500);
        }, 5000);
    });
});
