class HistoryItem {
  final int id;
  final String url;
  final String status;
  final String timestamp;

  HistoryItem({
    required this.id,
    required this.url,
    required this.status,
    required this.timestamp,
  });

  factory HistoryItem.fromJson(Map<String, dynamic> json) {
    return HistoryItem(
      id: json['id'] ?? 0,
      url: json['url'] ?? '',
      status: json['status'] ?? '',
      timestamp: json['timestamp'] ?? '',
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'url': url,
      'status': status,
      'timestamp': timestamp,
    };
  }

  // Helper to extract product name or slug from URL
  String get productName {
    try {
      final uri = Uri.parse(url);
      final paths = uri.pathSegments;
      if (url.contains('blinkit.com') && paths.length >= 2) {
        // e.g. blinkit.com/prn/product-slug/prid/id
        return paths[1].replaceAll('-', ' ');
      } else if (url.contains('zepto') && paths.isNotEmpty) {
        // e.g. zepto.com/p/product-slug/cid/id
        return paths.last.replaceAll('-', ' ');
      }
    } catch (_) {}
    return 'Product';
  }
}
