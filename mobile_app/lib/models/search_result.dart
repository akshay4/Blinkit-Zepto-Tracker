class SearchResult {
  final String name;
  final String details;
  final String url;
  final String status;

  SearchResult({
    required this.name,
    required this.details,
    required this.url,
    required this.status,
  });

  factory SearchResult.fromJson(Map<String, dynamic> json) {
    return SearchResult(
      name: json['name'] ?? '',
      details: json['details'] ?? '',
      url: json['url'] ?? '',
      status: json['status'] ?? 'UNKNOWN',
    );
  }
}
